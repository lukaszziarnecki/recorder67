import os
import sys
import queue
import time
import collections
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
import numpy as np
import sounddevice as sd

from PySide6.QtCore import QThread, Signal as pyqtSignal

from recorder.config import (
    SmartRecordState,
    RecordSourceMode,
    SAMPLE_RATE,
    AUDIO_CHANNELS,
    DEFAULT_AUTO_PAUSE_SEC,
    VAD_SPEECH_THRESHOLD,
    PRE_SPEECH_BUFFER_CHUNKS,
    RMS_SILENCE_THRESHOLD,
    DEFAULT_WHISPER_MODEL,
    SESSION_SPLIT_SILENCE_SEC,
    LIVE_BLOCK_MIN_SEC,
    LIVE_BLOCK_MAX_SEC,
    LIVE_BLOCK_SILENCE_CUT_SEC,
    get_record_source_mode,
    get_loopback_device_index,
    get_system_vad_speech_threshold,
    get_vad_speech_threshold,
    get_silence_alert_seconds,
    get_session_split_silence_sec
)
from recorder.audio.capture import save_wav_file, StreamingWavWriter
from recorder.audio.converter import resample_to_16k, prepare_audio_file
from recorder.audio.devices import HAS_PYAUDIOWPATCH, TargetAppAudioMonitor, clean_device_name
from recorder.core.vad import SileroVADDetector, is_silero_available
from recorder.core.transcriber import TranscriberEngine
from recorder.core.diarizer import DiarizationEngine, format_transcript_without_diarization

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None


class RealtimeAudioMixer:
    """
    Miesza dwa niezależne asynchroniczne strumienie audio 16kHz float32 (mikrofon + loopback)
    w czasie rzeczywistym do wspólnego pliku WAV bez opóźnień i przesterowań.
    """
    def __init__(self):
        import threading
        self.mic_chunks = []
        self.sys_chunks = []
        self.mic_buffer = np.array([], dtype=np.float32)
        self.sys_buffer = np.array([], dtype=np.float32)
        self.total_samples_popped = 0
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.mic_chunks = []
            self.sys_chunks = []
            self.mic_buffer = np.array([], dtype=np.float32)
            self.sys_buffer = np.array([], dtype=np.float32)
            self.total_samples_popped = 0

    def get_current_timeline_samples(self) -> int:
        """Zwraca łączną pozycję na osi czasu nagrywanego audio w próbkach (poziom pliku WAV)."""
        with self.lock:
            mic_len = len(self.mic_buffer) + sum(len(c) for c in self.mic_chunks)
            sys_len = len(self.sys_buffer) + sum(len(c) for c in self.sys_chunks)
            return int(self.total_samples_popped + max(mic_len, sys_len))

    def add_mic_chunk(self, chunk: np.ndarray):
        if chunk is None or len(chunk) == 0:
            return
        with self.lock:
            self.mic_chunks.append(chunk)

    def add_sys_chunk(self, chunk: np.ndarray):
        if chunk is None or len(chunk) == 0:
            return
        with self.lock:
            self.sys_chunks.append(chunk)

    def _flush_chunks_locked(self):
        """Scalenie oczekujących fragmentów audio z buforem bez ciągłego np.append."""
        if self.mic_chunks:
            if len(self.mic_buffer) > 0:
                self.mic_buffer = np.concatenate([self.mic_buffer] + self.mic_chunks)
            elif len(self.mic_chunks) == 1:
                self.mic_buffer = self.mic_chunks[0]
            else:
                self.mic_buffer = np.concatenate(self.mic_chunks)
            self.mic_chunks = []

        if self.sys_chunks:
            if len(self.sys_buffer) > 0:
                self.sys_buffer = np.concatenate([self.sys_buffer] + self.sys_chunks)
            elif len(self.sys_chunks) == 1:
                self.sys_buffer = self.sys_chunks[0]
            else:
                self.sys_buffer = np.concatenate(self.sys_chunks)
            self.sys_chunks = []

    def pop_mixed_frames(self, is_hybrid: bool, run_mic: bool, run_sys: bool, flush: bool = False) -> bytes:
        with self.lock:
            self._flush_chunks_locked()
            if not is_hybrid:
                if run_mic and len(self.mic_buffer) > 0:
                    data = self.mic_buffer
                    self.mic_buffer = np.array([], dtype=np.float32)
                    self.total_samples_popped += len(data)
                    int16_arr = (data * 32767.0).clip(-32768, 32767).astype(np.int16)
                    return int16_arr.tobytes()
                elif run_sys and len(self.sys_buffer) > 0:
                    data = self.sys_buffer
                    self.sys_buffer = np.array([], dtype=np.float32)
                    self.total_samples_popped += len(data)
                    int16_arr = (data * 32767.0).clip(-32768, 32767).astype(np.int16)
                    return int16_arr.tobytes()
                return b""

            # W trybie hybrydowym: Lewy kanał = Mikrofon, Prawy kanał = Dźwięk Systemu (Stereo 2-kanałowe)
            if run_mic and run_sys:
                len_diff = len(self.mic_buffer) - len(self.sys_buffer)
                if flush and len_diff != 0:
                    if len_diff > 0:
                        pad = np.zeros(len_diff, dtype=np.float32)
                        self.sys_buffer = np.concatenate([self.sys_buffer, pad]) if len(self.sys_buffer) > 0 else pad
                    else:
                        pad = np.zeros(-len_diff, dtype=np.float32)
                        self.mic_buffer = np.concatenate([self.mic_buffer, pad]) if len(self.mic_buffer) > 0 else pad
                elif abs(len_diff) > 4800:
                    if len_diff > 0:
                        pad = np.zeros(len_diff, dtype=np.float32)
                        self.sys_buffer = np.concatenate([self.sys_buffer, pad]) if len(self.sys_buffer) > 0 else pad
                    else:
                        pad = np.zeros(-len_diff, dtype=np.float32)
                        self.mic_buffer = np.concatenate([self.mic_buffer, pad]) if len(self.mic_buffer) > 0 else pad

            min_len = min(len(self.mic_buffer), len(self.sys_buffer))
            if min_len == 0:
                if len(self.mic_buffer) > 0 and len(self.sys_buffer) == 0 and (flush or len(self.mic_buffer) > 16000):
                    data = self.mic_buffer
                    self.mic_buffer = np.array([], dtype=np.float32)
                    self.total_samples_popped += len(data)
                    stereo = np.zeros((len(data), 2), dtype=np.float32)
                    stereo[:, 0] = data
                    return (stereo * 32767.0).clip(-32768, 32767).astype(np.int16).tobytes()
                elif len(self.sys_buffer) > 0 and len(self.mic_buffer) == 0 and (flush or len(self.sys_buffer) > 16000):
                    data = self.sys_buffer
                    self.sys_buffer = np.array([], dtype=np.float32)
                    self.total_samples_popped += len(data)
                    stereo = np.zeros((len(data), 2), dtype=np.float32)
                    stereo[:, 1] = data
                    return (stereo * 32767.0).clip(-32768, 32767).astype(np.int16).tobytes()
                return b""

            m_part = self.mic_buffer[:min_len]
            s_part = self.sys_buffer[:min_len]
            self.mic_buffer = self.mic_buffer[min_len:]
            self.sys_buffer = self.sys_buffer[min_len:]
            self.total_samples_popped += min_len

            # Zapis stereo: Kolumna 0 (Left) = Mic, Kolumna 1 (Right) = System
            stereo = np.empty((min_len, 2), dtype=np.float32)
            stereo[:, 0] = m_part
            stereo[:, 1] = s_part
            int16_arr = (stereo * 32767.0).clip(-32768, 32767).astype(np.int16)
            return int16_arr.tobytes()


class SmartAudioWorker(QThread):
    """
    Wątek przechwytywania dźwięku w czasie rzeczywistym z detekcją Silero VAD i buforowaniem.
    Obsługuje jednoczesny nasłuch mikrofonu biurowego oraz dźwięku systemu (WASAPI Loopback dla Discord/Teams),
    fizyczną separację kanałów, natywne próbkowanie oraz ciągły zapis wielogodzinny.
    """
    audio_level_signal = pyqtSignal(float)                     # Ogólny poziom RMS (0 - 100)
    dual_audio_level_signal = pyqtSignal(float, float)         # Poziom (mic_level, sys_level) 0 - 100
    vad_info_signal = pyqtSignal(bool, float, float)            # (is_speech, speech_prob, silence_sec)
    state_changed_signal = pyqtSignal(int)                     # SmartRecordState
    phrase_signal = pyqtSignal(np.ndarray, int, float)        # Frazy audio dla transkrypcji na żywo (16kHz, samplerate, start_sec)
    rolling_block_ready_signal = pyqtSignal(int, float, float, np.ndarray, str)  # (block_idx, start_sec, end_sec, audio_data, channel_source)
    session_split_signal = pyqtSignal(str)                     # Sygnał podziału na nową sesję spotkania (powód)
    silence_alert_signal = pyqtSignal(float, str)              # Ostrzeżenie strażnika ciszy (silence_sec, source_mode)
    error_signal = pyqtSignal(str)

    # Parametry okna bezpiecznego cięcia w tle (Safe VAD Boundary Handoff)
    MIN_BLOCK_DURATION_SEC = LIVE_BLOCK_MIN_SEC          # Szybki podgląd po min. 15s mowy
    SAFE_SILENCE_CUT_THRESHOLD_SEC = LIVE_BLOCK_SILENCE_CUT_SEC   # Wymagane min. 1.0s ciszy potwierdzonej przez Silero VAD
    MAX_BLOCK_DURATION_SEC = LIVE_BLOCK_MAX_SEC          # Maksymalny czas bloku 45 sekund
    OVERLAP_SAMPLES = int(0.5 * 16000)      # 0.5s nakładki akustycznej na styku

    def __init__(self, samplerate=SAMPLE_RATE, channels=AUDIO_CHANNELS, device_index=None,
                 loopback_device_index=None, source_mode=None, auto_pause_sec=DEFAULT_AUTO_PAUSE_SEC):
        super().__init__()
        self.target_samplerate = 16000
        self.actual_samplerate = samplerate or 16000
        self.samplerate = 16000
        self.channels = channels
        self.device_index = device_index
        self.loopback_device_index = loopback_device_index
        self.source_mode = source_mode or get_record_source_mode()
        self.auto_pause_sec = auto_pause_sec
        self.session_split_silence_sec = get_session_split_silence_sec()
        self.silence_alert_sec = get_silence_alert_seconds()
        self.silence_alert_emitted = False

        # Dwa niezależne detektory VAD dla mikrofonu oraz dla dźwięku systemu/Discorda
        mic_th = get_vad_speech_threshold()
        sys_th = get_system_vad_speech_threshold()
        self.vad_detector_mic = SileroVADDetector(speech_threshold=mic_th, default_samplerate=16000)
        self.vad_detector_sys = SileroVADDetector(speech_threshold=sys_th, default_samplerate=16000)
        self.vad_detector = self.vad_detector_mic  # Kompatybilność wsteczna

        self.audio_mixer = RealtimeAudioMixer()
        self.state = SmartRecordState.STOPPED
        self.frames = []
        self.silence_samples_count = 0
        self.continuous_silence_samples = 0
        self.session_split_silence_samples = 0
        self.pre_speech_chunks_mic = collections.deque(maxlen=15)
        self.pre_speech_chunks_sys = collections.deque(maxlen=15)
        self.session_has_speech = False
        self.wav_writer: Optional[StreamingWavWriter] = None
        self.save_wav_path: Optional[str] = None
        self._is_running = False

        # Bieżące poziomy głośności
        self.mic_level = 0.0
        self.sys_level = 0.0
        self.mic_muted = False
        self.sys_muted = False
        self.suppress_sys_until = 0.0

        # Monitor i izolacja wybranej aplikacji audio (np. Discord vs YouTube)
        self.target_app_filter = ""
        self.app_monitor = TargetAppAudioMonitor("")
        self.target_app_active_until = 0.0

        # Bufory bloków dla kanału mikrofonu
        self.session_start_datetime = datetime.now()
        self.current_mic_block_chunks = []
        self.mic_block_start_samples = 0
        self.total_mic_samples_added = 0
        self.mic_silence_samples = 0

        # Bufory bloków dla kanału systemu (Discord/Teams)
        self.current_sys_block_chunks = []
        self.sys_block_start_samples = 0
        self.total_sys_samples_added = 0
        self.sys_silence_samples = 0

        # Globalny licznik bloków
        self.block_index = 1
        self.current_block_chunks = self.current_mic_block_chunks  # Kompatybilność

        # Buforowanie fraz mowy
        self.current_phrase_chunks = []
        self.pre_speech_buffer = collections.deque(maxlen=PRE_SPEECH_BUFFER_CHUNKS)
        self.silence_in_phrase_samples = 0
        self.phrase_speech_detected = False
        import threading
        self._lock = threading.Lock()

    def set_auto_pause_sec(self, seconds: float):
        try:
            self.auto_pause_sec = float(seconds)
        except (ValueError, TypeError):
            self.auto_pause_sec = 5.0

    def set_session_split_silence_sec(self, seconds: float):
        try:
            self.session_split_silence_sec = float(seconds)
        except (ValueError, TypeError):
            self.session_split_silence_sec = SESSION_SPLIT_SILENCE_SEC

    def set_silence_alert_seconds(self, seconds: float):
        try:
            self.silence_alert_sec = max(0.0, float(seconds))
        except (ValueError, TypeError):
            self.silence_alert_sec = 600.0

    def reset_silence_alert(self):
        """Resetuje licznik ciągłej ciszy i odblokowuje kolejny alert strażnika ciszy."""
        with self._lock:
            self.continuous_silence_samples = 0
            self.silence_alert_emitted = False

    def suppress_sys_audio_for(self, duration_sec: float = 0.8):
        """Tymczasowo tłumi rejestrację dźwięku systemowego (np. podczas odtwarzania dzwonka powiadomienia programu)."""
        self.suppress_sys_until = time.time() + duration_sec

    def start_recording(self, device_index=None, loopback_device_index=None,
                        source_mode=None, target_app_filter: str = "", save_wav_path: Optional[str] = None,
                        mic_muted: bool = False, sys_muted: bool = False):
        self.device_index = device_index
        self.target_app_filter = target_app_filter or ""
        self.app_monitor.set_filter(self.target_app_filter)
        self.target_app_active_until = 0.0
        if loopback_device_index is not None:
            self.loopback_device_index = loopback_device_index
        elif self.loopback_device_index is None:
            self.loopback_device_index = get_loopback_device_index()

        if source_mode is not None:
            self.source_mode = source_mode
        else:
            self.source_mode = get_record_source_mode()

        self.frames = []
        self.silence_samples_count = 0
        self.continuous_silence_samples = 0
        self.silence_alert_emitted = False
        self.session_has_speech = False
        self.mic_muted = bool(mic_muted)
        self.sys_muted = bool(sys_muted)
        self.mic_level = 0.0
        self.sys_level = 0.0

        self.session_start_datetime = datetime.now()
        self.current_mic_block_chunks = []
        self.mic_block_start_samples = 0
        self.total_mic_samples_added = 0
        self.mic_silence_samples = 0

        self.current_sys_block_chunks = []
        self.sys_block_start_samples = 0
        self.total_sys_samples_added = 0
        self.sys_silence_samples = 0

        self.block_index = 1
        self.current_phrase_chunks = []
        self.pre_speech_buffer.clear()
        self.silence_in_phrase_samples = 0
        self.phrase_speech_detected = False
        self.audio_mixer.reset()

        if save_wav_path:
            self.save_wav_path = os.path.abspath(save_wav_path)
            is_hybrid = (self.source_mode == RecordSourceMode.HYBRID_DUAL) and HAS_PYAUDIOWPATCH
            wav_ch = 2 if is_hybrid else 1
            self.wav_writer = StreamingWavWriter(save_wav_path, channels=wav_ch, samplerate=16000)
        else:
            self.save_wav_path = None

        self.state = SmartRecordState.RECORDING_SPEECH
        self._is_running = True
        self.state_changed_signal.emit(self.state)
        self.start()

    def rotate_session_file(self, new_wav_path: str):
        """
        Zamyka bieżący plik sesji i natychmiast otwiera nowy plik WAV na dysku
        bez przerywania ciągłego nasłuchu mikrofonu i systemu.
        """
        if self.wav_writer:
            run_mic = self.source_mode in (RecordSourceMode.HYBRID_DUAL, RecordSourceMode.MIC_ONLY)
            run_sys = self.source_mode in (RecordSourceMode.HYBRID_DUAL, RecordSourceMode.SYSTEM_ONLY) and HAS_PYAUDIOWPATCH
            is_hybrid = (self.source_mode == RecordSourceMode.HYBRID_DUAL) and run_mic and run_sys
            rem_bytes = self.audio_mixer.pop_mixed_frames(is_hybrid, run_mic, run_sys, flush=True)
            if rem_bytes:
                try:
                    self.wav_writer.write_frames(rem_bytes)
                except Exception:
                    pass
            try:
                self.wav_writer.close()
            except Exception:
                pass
            self.wav_writer = None

        self._flush_mic_block()
        self._flush_sys_block()
        self.session_start_datetime = datetime.now()
        self.frames = []
        self.block_index = 1
        self.current_mic_block_chunks = []
        self.current_sys_block_chunks = []
        self.total_mic_samples_added = 0
        self.total_sys_samples_added = 0
        self.mic_silence_samples = 0
        self.sys_silence_samples = 0
        self.continuous_silence_samples = 0
        self.session_split_silence_samples = 0
        self.silence_alert_emitted = False
        self.session_has_speech = False
        self.audio_mixer.reset()

        if new_wav_path:
            self.save_wav_path = os.path.abspath(new_wav_path)
            is_hybrid = (self.source_mode == RecordSourceMode.HYBRID_DUAL) and HAS_PYAUDIOWPATCH
            wav_ch = 2 if is_hybrid else 1
            self.wav_writer = StreamingWavWriter(new_wav_path, channels=wav_ch, samplerate=16000)
        else:
            self.save_wav_path = None

    def get_remaining_blocks(self) -> List[Tuple[int, float, float, np.ndarray, str]]:
        """Zwraca wszystkie nieprzetworzone jeszcze bloki nagrania (dla obu kanałów) po kliknięciu Stop."""
        blocks = []
        from datetime import datetime, timedelta
        now_dt = datetime.now()
        s_dt = getattr(self, "session_start_datetime", now_dt)

        # Kanał mikrofonu
        if self.current_mic_block_chunks:
            try:
                mic_arr = np.concatenate(self.current_mic_block_chunks)
                if len(mic_arr) >= int(0.3 * 16000):
                    cur_dur = len(mic_arr) / 16000.0
                    end_sec = round(self.audio_mixer.get_current_timeline_samples() / 16000.0, 2)
                    start_sec = max(0.0, round(end_sec - cur_dur, 2))
                    blocks.append((self.block_index, start_sec, end_sec, mic_arr, "mic"))
                    self.block_index += 1
            except Exception:
                pass
            self.current_mic_block_chunks = []

        # Kanał systemu (Discord/Teams)
        if self.current_sys_block_chunks:
            try:
                sys_arr = np.concatenate(self.current_sys_block_chunks)
                if len(sys_arr) >= int(0.3 * 16000):
                    cur_dur = len(sys_arr) / 16000.0
                    end_sec = round(self.audio_mixer.get_current_timeline_samples() / 16000.0, 2)
                    st_sec = max(0.0, round(end_sec - cur_dur, 2))
                    blocks.append((self.block_index, st_sec, end_sec, sys_arr, "system"))
                    self.block_index += 1
            except Exception:
                pass
            self.current_sys_block_chunks = []

        return blocks

    def get_first_completed_block(self) -> Optional[Tuple[int, float, float, np.ndarray]]:
        """Kompatybilność wsteczna: zwraca pierwszy zaległy blok."""
        blocks = self.get_remaining_blocks()
        if blocks:
            b = blocks[0]
            return (b[0], b[1], b[2], b[3])
        return None

    def toggle_manual_pause(self):
        if self.state in [SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN, SmartRecordState.AUTO_PAUSED]:
            self.state = SmartRecordState.MANUAL_PAUSED
            self.state_changed_signal.emit(self.state)
        elif self.state == SmartRecordState.MANUAL_PAUSED:
            self.state = SmartRecordState.RECORDING_SPEECH
            self.silence_samples_count = 0
            self.state_changed_signal.emit(self.state)

    def stop_recording(self):
        with self._lock:
            self.state = SmartRecordState.STOPPED
            self._is_running = False
            if self.wav_writer:
                run_mic = self.source_mode in (RecordSourceMode.HYBRID_DUAL, RecordSourceMode.MIC_ONLY)
                run_sys = self.source_mode in (RecordSourceMode.HYBRID_DUAL, RecordSourceMode.SYSTEM_ONLY) and HAS_PYAUDIOWPATCH
                is_hybrid = (self.source_mode == RecordSourceMode.HYBRID_DUAL) and run_mic and run_sys
                rem_bytes = self.audio_mixer.pop_mixed_frames(is_hybrid, run_mic, run_sys, flush=True)
                if rem_bytes:
                    try:
                        self.wav_writer.write_frames(rem_bytes)
                    except Exception:
                        pass
                try:
                    self.wav_writer.close()
                except Exception:
                    pass
                self.wav_writer = None
            if self.phrase_speech_detected and self.current_phrase_chunks:
                try:
                    phrase_arr = np.concatenate(self.current_phrase_chunks)
                    if len(phrase_arr) >= int(0.3 * self.target_samplerate):
                        self.phrase_signal.emit(phrase_arr, self.target_samplerate, 0.0)
                except Exception:
                    pass
                self.current_phrase_chunks = []
            self.phrase_speech_detected = False
        self.state_changed_signal.emit(self.state)

    def update_target_app_filter(self, new_filter: str):
        """Dynamicznie aktualizuje filtr wybranej aplikacji audio w locie bez przerywania nagrywania."""
        with self._lock:
            clean = (new_filter or "").strip()
            self.target_app_filter = clean
            if hasattr(self, "app_monitor"):
                self.app_monitor.set_filter(clean)
            self.target_app_active_until = 0.0

    def _flush_mic_block(self):
        """Wypycha zgromadzone próbki audio z mikrofonu przed wyciszeniem lub zatrzymaniem."""
        if self.current_mic_block_chunks and self.state != SmartRecordState.STOPPED:
            try:
                block_arr = np.concatenate(self.current_mic_block_chunks)
                self.current_mic_block_chunks = []
                self.mic_silence_samples = 0
                cur_dur = len(block_arr) / 16000.0
                if cur_dur >= 0.5:
                    en_sec = round(self.audio_mixer.get_current_timeline_samples() / 16000.0, 2)
                    st_sec = max(0.0, round(en_sec - cur_dur, 2))
                    idx = self.block_index
                    self.block_index += 1
                    self.rolling_block_ready_signal.emit(idx, st_sec, en_sec, block_arr, "mic")
            except Exception as e:
                print(f"[SmartAudioWorker] Błąd flush mic block: {e}")

    def _flush_sys_block(self):
        """Wypycha zgromadzone próbki audio z systemu przed wyciszeniem lub zatrzymaniem."""
        if self.current_sys_block_chunks and self.state != SmartRecordState.STOPPED:
            try:
                arr = np.concatenate(self.current_sys_block_chunks)
                self.current_sys_block_chunks = []
                self.sys_silence_samples = 0
                cur_dur = len(arr) / 16000.0
                if cur_dur >= 0.5:
                    en_sec = round(self.audio_mixer.get_current_timeline_samples() / 16000.0, 2)
                    st_sec = max(0.0, round(en_sec - cur_dur, 2))
                    idx = self.block_index
                    self.block_index += 1
                    self.rolling_block_ready_signal.emit(idx, st_sec, en_sec, arr, "system")
            except Exception as e:
                print(f"[SmartAudioWorker] Błąd flush sys block: {e}")

    def set_mic_muted(self, muted: bool):
        """Wycisza lub przywraca nasłuch z mikrofonu w locie."""
        with self._lock:
            self.mic_muted = bool(muted)
            if self.mic_muted:
                self.mic_level = 0.0
                self._flush_mic_block()

    def set_sys_muted(self, muted: bool):
        """Wycisza lub przywraca nasłuch dźwięku systemu w locie."""
        with self._lock:
            self.sys_muted = bool(muted)
            if self.sys_muted:
                self.sys_level = 0.0
                self._flush_sys_block()

    def run(self):
        """Główna pętla rejestracji: uruchamia wątki mikrofonu oraz strumienia WASAPI Loopback."""
        run_mic = self.source_mode in (RecordSourceMode.HYBRID_DUAL, RecordSourceMode.MIC_ONLY)
        run_sys = self.source_mode in (RecordSourceMode.HYBRID_DUAL, RecordSourceMode.SYSTEM_ONLY) and HAS_PYAUDIOWPATCH

        self.mic_speech_active = False
        self.sys_speech_active = False
        self.vad_detector_mic.reset()
        self.vad_detector_sys.reset()

        # Inicjalizacja COM dla wątku roboczego (wymagana dla pycaw / monitorowania aplikacji)
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass

        # Inicjalizacja instancji PyAudio dla obu strumieni
        p_audio = None
        loop_stream = None
        mic_stream = None

        if HAS_PYAUDIOWPATCH and (run_mic or run_sys):
            try:
                time.sleep(0.1)  # Krótka pauza na zwolnienie endpointów WASAPI przez system Windows po Stop->Start
                p_audio = pyaudio.PyAudio()
            except Exception as e:
                print(f"[SmartAudioWorker] Błąd inicjalizacji PyAudio: {e}")

        # 1. Konfiguracja i uruchomienie strumienia Loopback (Dźwięk Systemu)
        if run_sys and p_audio:
            try:
                loopback_dev = None
                if self.loopback_device_index is not None and str(self.loopback_device_index).isdigit():
                    try:
                        dev_cand = p_audio.get_device_info_by_index(int(self.loopback_device_index))
                        if dev_cand.get("isLoopbackDevice", False) or "[Loopback]" in dev_cand.get("name", ""):
                            loopback_dev = dev_cand
                    except Exception:
                        pass
                if not loopback_dev:
                    try:
                        loopback_dev = p_audio.get_default_wasapi_loopback()
                    except Exception:
                        pass
                if not loopback_dev:
                    for cand in p_audio.get_loopback_device_info_generator():
                        loopback_dev = cand
                        break

                if loopback_dev:
                    sys_native_sr = int(loopback_dev.get("defaultSampleRate", 48000))
                    sys_channels = int(loopback_dev.get("maxInputChannels", 2))
                    last_loop_chunk_time = time.time()

                    def loopback_callback(in_data, frame_count, time_info, status):
                        nonlocal last_loop_chunk_time
                        if not self._is_running or self.state == SmartRecordState.STOPPED:
                            return (None, pyaudio.paAbort)
                        if self.state == SmartRecordState.MANUAL_PAUSED or not in_data or self.sys_muted or time.time() < self.suppress_sys_until:
                            if self.sys_muted or time.time() < self.suppress_sys_until:
                                self.sys_level = 0.0
                            return (None, pyaudio.paContinue)

                        last_loop_chunk_time = time.time()

                        # Izolacja wybranej aplikacji audio: jeśli wybrano konkretną aplikację (np. Discord),
                        # a ta aplikacja w tej chwili nie generuje dźwięku, odrzucamy próbki tła (np. YouTube)
                        if self.target_app_filter:
                            if time.time() > self.target_app_active_until:
                                self.sys_level = 0.0
                                return (None, pyaudio.paContinue)

                        try:
                            raw_np = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
                            if len(raw_np) == 0:
                                return (None, pyaudio.paContinue)

                            # Redukcja do mono (bezpieczne dla dowolnej długości bufora)
                            if sys_channels > 1:
                                usable_len = (len(raw_np) // sys_channels) * sys_channels
                                if usable_len > 0:
                                    mono = np.mean(raw_np[:usable_len].reshape(-1, sys_channels), axis=1)
                                else:
                                    mono = raw_np.flatten()
                            else:
                                mono = raw_np.flatten()

                            # Resampling do 16kHz
                            if sys_native_sr != 16000 and len(mono) > 0:
                                chunk_16k = resample_to_16k(mono, sys_native_sr)
                            else:
                                chunk_16k = mono

                            if len(chunk_16k) == 0:
                                return (None, pyaudio.paContinue)

                            # Obliczenie RMS i responsywnego poziomu VU (0 - 100)
                            norm_factor = float(np.linalg.norm(chunk_16k))
                            rms = (norm_factor / np.sqrt(len(chunk_16k))) if len(chunk_16k) > 0 else 0.0
                            calc_lvl = min(100.0, max(0.0, (rms ** 0.65) * 180.0))
                            self.sys_level = max(self.sys_level * 0.7, calc_lvl)

                            # Detekcja VAD (mowa z systemu / YouTube / Discord / komunikatorów)
                            is_speech, prob = self.vad_detector_sys.process_chunk(chunk_16k, samplerate=16000, rms_level=calc_lvl)

                            with self._lock:
                                if self.state != SmartRecordState.MANUAL_PAUSED:
                                    if is_speech:
                                        self.sys_speech_active = True
                                        was_paused = self.state in [SmartRecordState.AUTO_PAUSED, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]
                                        if was_paused:
                                            self.state = SmartRecordState.RECORDING_SPEECH
                                            self.state_changed_signal.emit(self.state)
                                            while self.pre_speech_chunks_sys:
                                                pre_c = self.pre_speech_chunks_sys.popleft()
                                                self.audio_mixer.add_sys_chunk(pre_c)
                                                self.current_sys_block_chunks.append(pre_c)
                                                self.total_sys_samples_added += len(pre_c)
                                        self.sys_silence_samples = 0
                                        self.silence_samples_count = 0
                                        self.continuous_silence_samples = 0
                                        self.session_split_silence_samples = 0
                                        self.session_has_speech = True
                                        self.silence_alert_emitted = False
                                    else:
                                        self.sys_silence_samples += len(chunk_16k)
                                        self.pre_speech_chunks_sys.append(chunk_16k.copy())

                                # Zapis próbek do miksera audio WAV
                                if self.state in [SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]:
                                    self.audio_mixer.add_sys_chunk(chunk_16k.copy())
                                    self.current_sys_block_chunks.append(chunk_16k.copy())
                                    self.total_sys_samples_added += len(chunk_16k)

                                # Cięcie bloków VAD dla kanału systemu
                                if self.current_sys_block_chunks and self.state != SmartRecordState.MANUAL_PAUSED and self.state != SmartRecordState.STOPPED:
                                    cur_len = sum(len(c) for c in self.current_sys_block_chunks)
                                    cur_dur = cur_len / 16000.0
                                    sil_dur = self.sys_silence_samples / 16000.0

                                    is_ready = (
                                        (cur_dur >= 6.0 and sil_dur >= 0.5) or
                                        (cur_dur >= 14.0 and sil_dur >= 0.3) or
                                        (cur_dur >= 25.0) or
                                        (cur_dur >= 2.0 and self.state == SmartRecordState.AUTO_PAUSED)
                                    )
                                    if is_ready and cur_dur >= 1.5:
                                        arr = np.concatenate(self.current_sys_block_chunks)
                                        self.current_sys_block_chunks = []
                                        self.sys_silence_samples = 0
                                        en_sec = round(self.audio_mixer.get_current_timeline_samples() / 16000.0, 2)
                                        st_sec = max(0.0, round(en_sec - cur_dur, 2))
                                        idx = self.block_index
                                        self.block_index += 1
                                        self.rolling_block_ready_signal.emit(idx, st_sec, en_sec, arr, "system")
                        except Exception:
                            pass
                        return (None, pyaudio.paContinue)

                    for attempt in range(3):
                        try:
                            loop_stream = p_audio.open(
                                format=pyaudio.paInt16,
                                channels=sys_channels,
                                rate=sys_native_sr,
                                input=True,
                                input_device_index=loopback_dev["index"],
                                frames_per_buffer=1024,
                                stream_callback=loopback_callback
                            )
                            loop_stream.start_stream()
                            break
                        except Exception as open_err:
                            if attempt < 2:
                                time.sleep(0.3)
                            else:
                                print(f"[SmartAudioWorker] Nie udało się otworzyć WASAPI Loopback po 3 próbach: {open_err}")
            except Exception as e:
                print(f"[SmartAudioWorker] Nie udało się otworzyć strumienia WASAPI Loopback: {e}")

        # 2. Konfiguracja i uruchomienie strumienia Mikrofonu (PyAudio)
        if run_mic and p_audio:
            try:
                mic_dev_info = None
                if self.device_index is not None and str(self.device_index).isdigit():
                    try:
                        mic_dev_info = p_audio.get_device_info_by_index(int(self.device_index))
                    except Exception:
                        pass
                if not mic_dev_info:
                    try:
                        mic_dev_info = p_audio.get_default_input_device_info()
                    except Exception:
                        pass

                if mic_dev_info:
                    mic_sr = int(mic_dev_info.get("defaultSampleRate", 16000))
                    mic_ch = max(1, int(mic_dev_info.get("maxInputChannels", 1)))
                    last_mic_chunk_time = time.time()

                    def mic_callback(in_data, frame_count, time_info, status):
                        nonlocal last_mic_chunk_time
                        if not self._is_running or self.state == SmartRecordState.STOPPED:
                            return (None, pyaudio.paAbort)
                        if self.state == SmartRecordState.MANUAL_PAUSED or not in_data or self.mic_muted:
                            if self.mic_muted:
                                self.mic_level = 0.0
                            return (None, pyaudio.paContinue)

                        last_mic_chunk_time = time.time()

                        try:
                            raw_np = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
                            if len(raw_np) == 0:
                                return (None, pyaudio.paContinue)

                            if mic_ch > 1:
                                usable_len = (len(raw_np) // mic_ch) * mic_ch
                                if usable_len > 0:
                                    mono = np.mean(raw_np[:usable_len].reshape(-1, mic_ch), axis=1)
                                else:
                                    mono = raw_np.flatten()
                            else:
                                mono = raw_np.flatten()

                            if mic_sr != 16000 and len(mono) > 0:
                                chunk_16k = resample_to_16k(mono, mic_sr)
                            else:
                                chunk_16k = mono

                            if len(chunk_16k) == 0:
                                return (None, pyaudio.paContinue)

                            norm_factor = float(np.linalg.norm(chunk_16k))
                            rms = (norm_factor / np.sqrt(len(chunk_16k))) if len(chunk_16k) > 0 else 0.0
                            calc_lvl = min(100.0, max(0.0, (rms ** 0.65) * 180.0))
                            self.mic_level = max(self.mic_level * 0.7, calc_lvl)

                            is_speech, speech_prob = self.vad_detector_mic.process_chunk(chunk_16k, samplerate=16000, rms_level=calc_lvl)

                            with self._lock:
                                if self.state != SmartRecordState.MANUAL_PAUSED:
                                    if is_speech:
                                        self.mic_speech_active = True
                                        was_paused = self.state in [SmartRecordState.AUTO_PAUSED, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]
                                        if was_paused:
                                            self.state = SmartRecordState.RECORDING_SPEECH
                                            self.state_changed_signal.emit(self.state)
                                            while self.pre_speech_chunks_mic:
                                                pre_c = self.pre_speech_chunks_mic.popleft()
                                                self.audio_mixer.add_mic_chunk(pre_c)
                                                self.current_mic_block_chunks.append(pre_c)
                                                self.total_mic_samples_added += len(pre_c)
                                        self.silence_samples_count = 0
                                        self.continuous_silence_samples = 0
                                        self.session_split_silence_samples = 0
                                        self.mic_silence_samples = 0
                                        self.session_has_speech = True
                                        self.silence_alert_emitted = False
                                    else:
                                        self.mic_silence_samples += len(chunk_16k)
                                        self.pre_speech_chunks_mic.append(chunk_16k.copy())

                                # Zapis próbek do miksera audio WAV
                                if self.state in [SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]:
                                    self.audio_mixer.add_mic_chunk(chunk_16k.copy())
                                    self.current_mic_block_chunks.append(chunk_16k.copy())
                                    self.total_mic_samples_added += len(chunk_16k)

                                # Cięcie bloków VAD dla kanału mikrofonu
                                if self.current_mic_block_chunks and self.state != SmartRecordState.MANUAL_PAUSED and self.state != SmartRecordState.STOPPED:
                                    cur_len = sum(len(c) for c in self.current_mic_block_chunks)
                                    cur_dur = cur_len / 16000.0
                                    sil_dur = self.mic_silence_samples / 16000.0

                                    is_ready = (
                                        (cur_dur >= 6.0 and sil_dur >= 0.5) or
                                        (cur_dur >= 14.0 and sil_dur >= 0.3) or
                                        (cur_dur >= 25.0) or
                                        (cur_dur >= 2.0 and self.state == SmartRecordState.AUTO_PAUSED)
                                    )
                                    if is_ready and cur_dur >= 1.5:
                                        block_arr = np.concatenate(self.current_mic_block_chunks)
                                        self.current_mic_block_chunks = []
                                        self.mic_silence_samples = 0
                                        end_sec = round(self.audio_mixer.get_current_timeline_samples() / 16000.0, 2)
                                        start_sec = max(0.0, round(end_sec - cur_dur, 2))
                                        idx = self.block_index
                                        self.block_index += 1
                                        self.rolling_block_ready_signal.emit(idx, start_sec, end_sec, block_arr, "mic")
                        except Exception:
                            pass
                        return (None, pyaudio.paContinue)

                    last_mic_err = None
                    target_clean_name = clean_device_name(mic_dev_info.get("name", "")) if (mic_dev_info and isinstance(mic_dev_info, dict)) else ""
                    candidate_mic_devices = [mic_dev_info] if mic_dev_info else []
                    try:
                        c_val = p_audio.get_device_count()
                        dev_cnt = c_val if isinstance(c_val, int) else 0
                        for c_idx in range(dev_cnt):
                            if mic_dev_info and c_idx == mic_dev_info.get("index"):
                                continue
                            cand = p_audio.get_device_info_by_index(c_idx)
                            if cand.get("maxInputChannels", 0) > 0 and not cand.get("isLoopbackDevice", False):
                                if target_clean_name and clean_device_name(cand.get("name", "")) == target_clean_name:
                                    candidate_mic_devices.append(cand)
                    except Exception:
                        pass

                    for cand_dev in candidate_mic_devices:
                        c_sr = int(cand_dev.get("defaultSampleRate", 16000))
                        c_ch = max(1, int(cand_dev.get("maxInputChannels", 1)))
                        for attempt in range(3):
                            try:
                                mic_stream = p_audio.open(
                                    format=pyaudio.paInt16,
                                    channels=c_ch,
                                    rate=c_sr,
                                    input=True,
                                    input_device_index=cand_dev["index"],
                                    frames_per_buffer=1024,
                                    stream_callback=mic_callback
                                )
                                mic_stream.start_stream()
                                mic_dev_info = cand_dev
                                mic_sr = c_sr
                                mic_ch = c_ch
                                last_mic_err = None
                                break
                            except Exception as open_err:
                                last_mic_err = open_err
                                print(f"[SmartAudioWorker] Próba {attempt + 1}/3 otwarcia mikrofonu ({cand_dev.get('name')}, idx {cand_dev.get('index')}) nie powiodła się: {open_err}")
                                if attempt < 2:
                                    time.sleep(0.3)
                        if mic_stream is not None:
                            break

                    if mic_stream is None:
                        raise RuntimeError(f"Błąd otwarcia mikrofonu po 3 próbach: {last_mic_err}")
            except Exception as e:
                print(f"[SmartAudioWorker] Nie udało się otworzyć mikrofonu w PyAudio: {e}")
                dev_name = mic_dev_info.get("name", "Nieznane urządzenie") if (mic_dev_info and isinstance(mic_dev_info, dict)) else "Brak urządzenia"
                self.error_signal.emit(f"Nie udało się otworzyć mikrofonu ({dev_name}): {e}")

        if (run_mic and mic_stream is None) and (not run_sys or loop_stream is None):
            self._is_running = False
            return

        def _reopen_mic_stream():
            nonlocal mic_stream, p_audio, mic_dev_info, mic_sr, mic_ch, last_mic_chunk_time
            print("[SmartAudioWorker WATCHDOG] Strumień mikrofonu był nieaktywny lub zablokowany! Restartowanie...")
            if mic_stream is not None:
                try:
                    if mic_stream.is_active():
                        mic_stream.stop_stream()
                except Exception:
                    pass
                try:
                    mic_stream.close()
                except Exception:
                    pass
                mic_stream = None

            target_name = clean_device_name(mic_dev_info.get("name", "")) if (mic_dev_info and isinstance(mic_dev_info, dict)) else ""
            reopen_cands = []
            if mic_dev_info and isinstance(mic_dev_info, dict) and "index" in mic_dev_info:
                reopen_cands.append(mic_dev_info)

            try:
                c_val = p_audio.get_device_count()
                dev_cnt = c_val if isinstance(c_val, int) else 0
                for c_idx in range(dev_cnt):
                    if mic_dev_info and c_idx == mic_dev_info.get("index"):
                        continue
                    cand = p_audio.get_device_info_by_index(c_idx)
                    if cand.get("maxInputChannels", 0) > 0 and not cand.get("isLoopbackDevice", False):
                        if target_name and clean_device_name(cand.get("name", "")) == target_name:
                            reopen_cands.append(cand)
            except Exception:
                pass

            for cand_dev in reopen_cands:
                c_sr = int(cand_dev.get("defaultSampleRate", 16000))
                c_ch = max(1, int(cand_dev.get("maxInputChannels", 1)))
                try:
                    new_s = p_audio.open(
                        format=pyaudio.paInt16,
                        channels=c_ch,
                        rate=c_sr,
                        input=True,
                        input_device_index=cand_dev["index"],
                        frames_per_buffer=1024,
                        stream_callback=mic_callback
                    )
                    new_s.start_stream()
                    if new_s.is_active():
                        mic_stream = new_s
                        mic_dev_info = cand_dev
                        mic_sr = c_sr
                        mic_ch = c_ch
                        last_mic_chunk_time = time.time()
                        print(f"[SmartAudioWorker WATCHDOG] Pomyślnie przywrócono strumień mikrofonu ({cand_dev.get('name')}, idx {cand_dev.get('index')})!")
                        return True
                    else:
                        try:
                            new_s.close()
                        except Exception:
                            pass
                except Exception as ro_err:
                    print(f"[SmartAudioWorker WATCHDOG] Błąd otwarcia mikrofonu (indeks {cand_dev.get('index')}): {ro_err}")

            # Jeśli ponowne otwarcie na p_audio się nie powiodło (np. odłączenie odbiornika USB):
            print("[SmartAudioWorker WATCHDOG] Re-inicjalizacja instancji PyAudio po uśpieniu lub resecie USB...")
            try:
                if loop_stream is not None:
                    try:
                        if loop_stream.is_active():
                            loop_stream.stop_stream()
                        loop_stream.close()
                    except Exception:
                        pass
                    loop_stream = None
                p_audio.terminate()
            except Exception:
                pass
            time.sleep(0.3)
            try:
                p_audio = pyaudio.PyAudio()
                new_dev = None
                if target_name:
                    c_val = p_audio.get_device_count()
                    dev_cnt = c_val if isinstance(c_val, int) else 0
                    for idx in range(dev_cnt):
                        cand = p_audio.get_device_info_by_index(idx)
                        if cand.get("maxInputChannels", 0) > 0 and not cand.get("isLoopbackDevice", False):
                            if clean_device_name(cand.get("name", "")) == target_name:
                                new_dev = cand
                                break
                if not new_dev:
                    try:
                        new_dev = p_audio.get_default_input_device_info()
                    except Exception:
                        pass
                if new_dev:
                    mic_dev_info = new_dev
                    mic_sr = int(mic_dev_info.get("defaultSampleRate", 16000))
                    mic_ch = max(1, int(mic_dev_info.get("maxInputChannels", 1)))
                    new_s = p_audio.open(
                        format=pyaudio.paInt16,
                        channels=mic_ch,
                        rate=mic_sr,
                        input=True,
                        input_device_index=mic_dev_info["index"],
                        frames_per_buffer=1024,
                        stream_callback=mic_callback
                    )
                    new_s.start_stream()
                    if new_s.is_active():
                        mic_stream = new_s
                        last_mic_chunk_time = time.time()
                        print(f"[SmartAudioWorker WATCHDOG] Mikrofon pomyślnie zreaktywowany po re-inicjalizacji: {mic_dev_info.get('name')}")
                        if run_sys and not loop_stream:
                            _reopen_loop_stream()
                        return True
            except Exception as re_init_err:
                print(f"[SmartAudioWorker WATCHDOG] Błąd ponownej inicjalizacji PyAudio: {re_init_err}")
            return False

        def _reopen_loop_stream():
            nonlocal loop_stream, p_audio, loopback_dev, sys_native_sr, sys_channels, last_loop_chunk_time
            print("[SmartAudioWorker WATCHDOG] Restartowanie strumienia WASAPI Loopback...")
            if loop_stream is not None:
                try:
                    if loop_stream.is_active():
                        loop_stream.stop_stream()
                except Exception:
                    pass
                try:
                    loop_stream.close()
                except Exception:
                    pass
                loop_stream = None

            try:
                def_l = None
                try:
                    def_l = p_audio.get_default_wasapi_loopback()
                except Exception:
                    pass
                if def_l and (not loopback_dev or loopback_dev.get("index") != def_l.get("index")):
                    loopback_dev = def_l
                    sys_native_sr = int(loopback_dev.get("defaultSampleRate", 48000))
                    sys_channels = int(loopback_dev.get("maxInputChannels", 2))

                if loopback_dev:
                    new_l_stream = p_audio.open(
                        format=pyaudio.paInt16,
                        channels=sys_channels,
                        rate=sys_native_sr,
                        input=True,
                        input_device_index=loopback_dev["index"],
                        frames_per_buffer=1024,
                        stream_callback=loopback_callback
                    )
                    new_l_stream.start_stream()
                    if new_l_stream.is_active():
                        loop_stream = new_l_stream
                        last_loop_chunk_time = time.time()
                        print(f"[SmartAudioWorker WATCHDOG] Pomyślnie zrestartowano strumień loopback: {loopback_dev.get('name')}")
                        return True
            except Exception as l_err:
                print(f"[SmartAudioWorker WATCHDOG] Błąd restartu strumienia loopback: {l_err}")
            return False

        # Pętla monitorowania poziomów, stanu ciszy i strumieniowego zapisu zmiksowanego audio
        last_watchdog_check = time.time()
        try:
            while self._is_running:
                # Opróżnienie miksera i ciągły zapis zsynchronizowanego audio do pliku WAV
                is_hybrid = (self.source_mode == RecordSourceMode.HYBRID_DUAL) and run_mic and run_sys
                mixed_bytes = self.audio_mixer.pop_mixed_frames(is_hybrid, run_mic, run_sys)
                if mixed_bytes and self.state in [SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]:
                    if self.wav_writer:
                        self.wav_writer.write_frames(mixed_bytes)
                    else:
                        self.frames.append(mixed_bytes)

                # Watchdog aktywności strumieni audio (samoczynne wznawianie w razie uśpienia przez sterownik)
                now_tick = time.time()
                if now_tick - last_watchdog_check >= 1.5:
                    last_watchdog_check = now_tick
                    if run_mic and mic_stream is not None and self.state not in (SmartRecordState.STOPPED, SmartRecordState.MANUAL_PAUSED) and not self.mic_muted:
                        mic_failed = False
                        try:
                            if not mic_stream.is_active():
                                mic_failed = True
                            elif (now_tick - last_mic_chunk_time) > 4.0:
                                print(f"[SmartAudioWorker WATCHDOG] Stagnacja bufora mikrofonu (> {now_tick - last_mic_chunk_time:.1f}s bez danych)!")
                                mic_failed = True
                        except Exception:
                            mic_failed = True

                        if mic_failed:
                            quick_ok = False
                            try:
                                if not mic_stream.is_active():
                                    mic_stream.start_stream()
                                    if mic_stream.is_active() and (now_tick - last_mic_chunk_time) <= 2.0:
                                        quick_ok = True
                            except Exception:
                                pass
                            if not quick_ok:
                                _reopen_mic_stream()

                    if run_sys and loop_stream is not None and self.state not in (SmartRecordState.STOPPED, SmartRecordState.MANUAL_PAUSED) and not self.sys_muted:
                        loop_failed = False
                        try:
                            if not loop_stream.is_active():
                                loop_failed = True
                            elif (now_tick - last_loop_chunk_time) > 4.0:
                                loop_failed = True
                        except Exception:
                            loop_failed = True

                        if loop_failed:
                            quick_l_ok = False
                            try:
                                if not loop_stream.is_active():
                                    loop_stream.start_stream()
                                    if loop_stream.is_active() and (now_tick - last_loop_chunk_time) <= 2.0:
                                        quick_l_ok = True
                            except Exception:
                                pass
                            if not quick_l_ok:
                                _reopen_loop_stream()

                # Emisja poziomów VU Meter
                m_lvl = float(self.mic_level)
                s_lvl = float(self.sys_level)
                self.dual_audio_level_signal.emit(m_lvl, s_lvl)
                self.audio_level_signal.emit(float(max(m_lvl, s_lvl)))

                # Globalny licznik ciszy (jeśli na obu kanałach jest cisza)
                if not self.mic_speech_active and not self.sys_speech_active:
                    self.silence_samples_count += 640  # 40ms przy 16kHz
                    self.continuous_silence_samples += 640
                    self.session_split_silence_samples += 640
                self.mic_speech_active = False
                self.sys_speech_active = False

                # Sprawdzenie ostrzeżenia strażnika ciszy (brak dźwięku przez zdefiniowany czas)
                if self.silence_alert_sec > 0 and self.state != SmartRecordState.MANUAL_PAUSED:
                    cont_sil_sec = float(self.continuous_silence_samples / 16000.0)
                    if cont_sil_sec >= self.silence_alert_sec and not self.silence_alert_emitted:
                        self.silence_alert_emitted = True
                        self.silence_alert_signal.emit(cont_sil_sec, self.source_mode)

                # Sprawdzenie podziału sesji po długiej ciszy (niezależny licznik od alertów)
                if self.session_has_speech and self.session_split_silence_sec > 0:
                    split_sil_sec = float(self.session_split_silence_samples / 16000.0)
                    if split_sil_sec >= self.session_split_silence_sec:
                        self.session_has_speech = False
                        self.session_split_silence_samples = 0
                        self.continuous_silence_samples = 0
                        mins = int(self.session_split_silence_sec // 60)
                        self.session_split_signal.emit(f"Cisza > {mins} min")

                # Sprawdzenie auto-pauzy
                if self.state != SmartRecordState.MANUAL_PAUSED:
                    cur_sil_sec = float(self.silence_samples_count / 16000.0)
                    if cur_sil_sec >= self.auto_pause_sec:
                        if self.state != SmartRecordState.AUTO_PAUSED:
                            self.state = SmartRecordState.AUTO_PAUSED
                            self.state_changed_signal.emit(self.state)
                    elif cur_sil_sec > 0.6:
                        if self.state != SmartRecordState.RECORDING_SILENCE_COUNTDOWN:
                            self.state = SmartRecordState.RECORDING_SILENCE_COUNTDOWN
                            self.state_changed_signal.emit(self.state)

                    self.vad_info_signal.emit(
                        bool(m_lvl > 2.0 or s_lvl > 2.0),
                        float(max(m_lvl, s_lvl) / 100.0),
                        float(cur_sil_sec)
                    )

                # Sprawdzenie aktywności docelowej aplikacji audio (izolacja procesu np. Discord vs YouTube)
                if self.target_app_filter and run_sys:
                    if self.app_monitor.is_target_app_playing():
                        self.target_app_active_until = time.time() + 0.40

                # Wygaszanie poziomów VU
                self.mic_level *= 0.92
                self.sys_level *= 0.92
                self.msleep(40)
        finally:
            if hasattr(self, "app_monitor"):
                try:
                    self.app_monitor.meters = []
                except Exception:
                    pass
            if mic_stream:
                try:
                    if mic_stream.is_active():
                        mic_stream.stop_stream()
                    mic_stream.close()
                except Exception:
                    pass
            if loop_stream:
                try:
                    if loop_stream.is_active():
                        loop_stream.stop_stream()
                    loop_stream.close()
                except Exception:
                    pass
            if p_audio:
                try:
                    time.sleep(0.05)
                    p_audio.terminate()
                except Exception:
                    pass
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass

    def save_wav(self, file_path: str) -> bool:
        if self.wav_writer:
            run_mic = self.source_mode in (RecordSourceMode.HYBRID_DUAL, RecordSourceMode.MIC_ONLY)
            run_sys = self.source_mode in (RecordSourceMode.HYBRID_DUAL, RecordSourceMode.SYSTEM_ONLY) and HAS_PYAUDIOWPATCH
            is_hybrid = (self.source_mode == RecordSourceMode.HYBRID_DUAL) and run_mic and run_sys
            rem_bytes = self.audio_mixer.pop_mixed_frames(is_hybrid, run_mic, run_sys, flush=True)
            if rem_bytes:
                try:
                    self.wav_writer.write_frames(rem_bytes)
                except Exception:
                    pass
            try:
                self.wav_writer.close()
            except Exception:
                pass
            self.wav_writer = None

        norm_target = os.path.abspath(file_path) if file_path else ""
        norm_source = os.path.abspath(self.save_wav_path) if getattr(self, "save_wav_path", None) else ""

        # Jeśli strumieniowaliśmy bezpośrednio na dysk do innego pliku docelowego, skopiujmy zawartość
        if norm_source and norm_target and norm_source != norm_target and os.path.exists(norm_source) and os.path.getsize(norm_source) > 44:
            try:
                import shutil
                shutil.copy2(norm_source, norm_target)
            except Exception as e:
                import logging
                logging.getLogger("recorder").warning(f"Błąd kopiowania nagranego pliku audio z '{norm_source}' do '{norm_target}': {e}")

        if os.path.exists(file_path) and os.path.getsize(file_path) > 44:
            self.frames = []
            return True

        is_hybrid = (self.source_mode == RecordSourceMode.HYBRID_DUAL) and HAS_PYAUDIOWPATCH
        wav_ch = 2 if is_hybrid else 1
        saved = save_wav_file(file_path, self.frames, channels=wav_ch, samplerate=16000)
        self.frames = []
        return saved



class LiveTranscriptionWorker(QThread):
    """
    Wątek przetwarzający frazy audio w czasie rzeczywistym przy użyciu faster-whisper.
    """
    phrase_transcribed_signal = pyqtSignal(str, str)  # (time_str, phrase_text)
    status_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, model_size=DEFAULT_WHISPER_MODEL):
        super().__init__()
        self.model_size = model_size
        self.audio_queue = queue.Queue()
        self._is_running = False
        self.transcriber = TranscriberEngine(model_size=self.model_size)

    def add_phrase_chunk(self, audio_data, samplerate, start_sec: float = 0.0):
        if self._is_running:
            self.audio_queue.put((audio_data, samplerate, start_sec))

    def stop(self):
        self._is_running = False
        # Opróżnij kolejkę, aby natychmiast przerwać przetwarzanie zaległych chunków
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except Exception:
                break
        self.audio_queue.put(None)

    def run(self):
        self._is_running = True
        recent_context = ""
        try:
            self.status_signal.emit(f"Ładowanie modelu Whisper ({self.model_size})...")
            self.transcriber.load_model()
            self.status_signal.emit(f"Whisper Na Żywo [{self.model_size}]: GOTOWY")

            while self._is_running:
                try:
                    item = self.audio_queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if item is None:
                    break

                audio_data, samplerate, start_sec = item
                if audio_data is None or len(audio_data) == 0:
                    self.audio_queue.task_done()
                    continue

                # 1. Konwersja do float32 [-1.0, 1.0]
                if audio_data.dtype == np.int16:
                    audio_float = audio_data.astype(np.float32) / 32768.0
                else:
                    audio_float = audio_data.astype(np.float32)

                # 2. Resampling do 16kHz
                if samplerate != 16000:
                    audio_float = resample_to_16k(audio_float, samplerate)

                # 3. Odfiltrowanie cichego szumu tła (RMS)
                rms = np.sqrt(np.mean(audio_float ** 2)) if len(audio_float) > 0 else 0.0
                if rms < RMS_SILENCE_THRESHOLD:
                    self.audio_queue.task_done()
                    continue

                # 4. Transkrypcja frazy z pamięcią kontekstu
                phrase_text = self.transcriber.transcribe_live_chunk(audio_float, language="pl", context_prompt=recent_context)
                if phrase_text:
                    recent_context = (recent_context + " " + phrase_text).strip()[-250:]
                    m, s = int(start_sec // 60), int(start_sec % 60)
                    time_str = f"{m:02d}:{s:02d}"
                    self.phrase_transcribed_signal.emit(time_str, phrase_text)

                self.audio_queue.task_done()

        except Exception as e:
            self.error_signal.emit(f"Błąd transkrypcji na żywo: {e}")


class TranscriptionWorker(QThread):
    """
    Wątek wykonujący pełną transkrypcję z opcjonalną diaryzacją mówców (PyAnnote).
    """
    progress_signal = pyqtSignal(int, str)
    preliminary_signal = pyqtSignal(str, str, list)  # Wstępna transkrypcja z Whispera przed diaryzacją
    finished_signal = pyqtSignal(str, str, list)     # (html_text, plain_text, turns)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        audio_path: str,
        hf_token: str,
        model_size: str = DEFAULT_WHISPER_MODEL,
        enable_diarization: bool = True,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None
    ):
        super().__init__()
        self.audio_path = audio_path
        self.hf_token = hf_token
        self.model_size = model_size
        self.enable_diarization = enable_diarization
        self.num_speakers = num_speakers
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers

    def run(self):
        try:
            self.progress_signal.emit(10, f"Ładowanie modelu Whisper ({self.model_size})...")
            transcriber = TranscriberEngine(model_size=self.model_size)
            
            self.progress_signal.emit(30, "Trwa transkrypcja audio...")
            transcript_words = transcriber.transcribe_file_with_words(self.audio_path, language="pl")

            if not transcript_words:
                self.finished_signal.emit("Brak wykrytej mowy w nagraniu.", "Brak wykrytej mowy w nagraniu.", [])
                return

            # Wstępne sformatowanie transkrypcji (gwarancja braku utraty danych)
            init_html, init_plain, init_turns = format_transcript_without_diarization(transcript_words)
            self.preliminary_signal.emit(init_html, init_plain, init_turns)

            # Jeśli wyłączono diaryzację lub brak tokena HuggingFace
            if not self.enable_diarization or not self.hf_token:
                self.progress_signal.emit(100, "Gotowe!")
                self.finished_signal.emit(init_html, init_plain, init_turns)
                return

            # Pełna diaryzacja mówców (PyAnnote)
            speaker_info = ""
            if self.num_speakers:
                speaker_info = f" (dokładnie {self.num_speakers} os.)"
            elif self.max_speakers:
                speaker_info = f" (max {self.max_speakers} os.)"

            self.progress_signal.emit(60, f"Ładowanie modelu PyAnnote{speaker_info}...")
            diarizer = DiarizationEngine(hf_token=self.hf_token)

            def on_diar_progress(pct: int, msg: str):
                self.progress_signal.emit(pct, msg)

            self.progress_signal.emit(65, f"Analiza głosów mówców{speaker_info}...")
            final_html, final_plain, turns = diarizer.process(
                self.audio_path,
                transcript_words,
                batch_size=32,
                num_speakers=self.num_speakers,
                min_speakers=self.min_speakers,
                max_speakers=self.max_speakers,
                progress_callback=on_diar_progress
            )

            self.progress_signal.emit(100, "Gotowe!")
            self.finished_signal.emit(final_html, final_plain, turns)

        except Exception as e:
            self.error_signal.emit(str(e))


class FileProcessingWorker(QThread):
    """
    Wątek asynchroniczny przetwarzający wgrany z dysku plik audio lub wideo (np. .mp4 ze spotkania):
    1. Normalizacja do formatu WAV 16kHz mono (za pomocą wbudowanego imageio-ffmpeg)
    2. Transkrypcja Faster-Whisper z wybranym modelem i wskaźnikiem postępu w locie + natychmiastowy autozapis TXT
    3. Opcjonalna diaryzacja mówców PyAnnote (batch_size=32, hook postępu w UI, automatyczna aktualizacja TXT)
    """
    progress_signal = pyqtSignal(int, str)
    preliminary_signal = pyqtSignal(str, str, str, list)  # (html_text, plain_text, prepared_wav_path, turns)
    finished_signal = pyqtSignal(str, str, str, list)     # (html_text, plain_text, prepared_wav_path, turns)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        input_file_path: str,
        recordings_dir: str,
        hf_token: Optional[str] = None,
        model_size: str = DEFAULT_WHISPER_MODEL,
        enable_diarization: bool = True,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None
    ):
        super().__init__()
        self.input_file_path = input_file_path
        self.recordings_dir = recordings_dir
        self.hf_token = hf_token
        self.model_size = model_size
        self.enable_diarization = enable_diarization
        self.num_speakers = num_speakers
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers

    def run(self):
        try:
            print("\n" + "="*70)
            print(f"📂 [PLIK] Rozpoczęto przetwarzanie pliku: {self.input_file_path}")
            print("="*70)

            # ETAP 1: Konwersja i normalizacja formatu audio
            self.progress_signal.emit(5, "Etap 1/3: Ekstrakcja i normalizacja dźwięku do 16kHz WAV...")
            prepared_wav_path, duration_sec = prepare_audio_file(self.input_file_path, self.recordings_dir)

            mins = int(duration_sec // 60)
            secs = int(duration_sec % 60)
            print(f"🎵 [PLIK] Audio przygotowane: {prepared_wav_path} (Długość: {mins}m {secs}s)")
            self.progress_signal.emit(15, f"Etap 1/3: Audio gotowe ({mins}m {secs}s). Ładowanie Whisper ({self.model_size})...")

            # ETAP 2: Transkrypcja Faster-Whisper z raportowaniem postępu
            transcriber = TranscriberEngine(model_size=self.model_size)

            def on_whisper_progress(ratio: float, cur_time_sec: float):
                pct = int(20 + ratio * 40)
                cur_mins = int(cur_time_sec // 60)
                cur_secs = int(cur_time_sec % 60)
                self.progress_signal.emit(
                    pct,
                    f"Etap 2/3: Transkrypcja Whisper ({cur_mins}m {cur_secs}s / {mins}m {secs}s - {int(ratio * 100)}%)..."
                )

            self.progress_signal.emit(20, "Etap 2/3: Rozpoczynanie transkrypcji mowy...")
            transcript_words = transcriber.transcribe_file_with_words(
                prepared_wav_path,
                language="pl",
                progress_callback=on_whisper_progress,
                duration_sec=duration_sec
            )

            if not transcript_words:
                msg = "Nie wykryto zrozumiałej mowy w przesłanym pliku audio."
                print("⚠️ [PLIK] Nie wykryto słów w pliku audio.")
                self.finished_signal.emit(msg, msg, prepared_wav_path, [])
                return

            # Wczesne sformatowanie i NATYCHMIASTOWY zapis wstępnego pliku TXT na dysk
            init_html, init_plain, init_turns = format_transcript_without_diarization(transcript_words)
            
            base_name = os.path.basename(prepared_wav_path)
            file_stem = os.path.splitext(base_name)[0]
            txt_dir = self.recordings_dir.replace("recordings", "transcriptions")
            os.makedirs(txt_dir, exist_ok=True)
            txt_path = os.path.join(txt_dir, f"transkrypcja_{file_stem}.txt")
            
            try:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(init_plain)
                print(f"💾 [AUTOZAPIS] Zapisano wstępną transkrypcję do: {txt_path}")
            except Exception as save_err:
                print(f"⚠️ [AUTOZAPIS] Błąd wstępnego zapisu TXT: {save_err}")

            # Wyemitowanie wstępnego tekstu do GUI (użytkownik już ma podgląd pełnego tekstu!)
            self.preliminary_signal.emit(init_html, init_plain, prepared_wav_path, init_turns)

            # ETAP 3: Diaryzacja PyAnnote lub zakończenie
            turns = []
            if self.enable_diarization and self.hf_token and self.hf_token.strip():
                speaker_info = ""
                if self.num_speakers:
                    speaker_info = f" (dokładnie {self.num_speakers} os.)"
                elif self.max_speakers:
                    speaker_info = f" (max {self.max_speakers} os.)"

                self.progress_signal.emit(60, f"Etap 3/3: Ładowanie modelu PyAnnote{speaker_info}...")
                diarizer = DiarizationEngine(hf_token=self.hf_token.strip())

                def on_diar_progress(pct: int, msg: str):
                    self.progress_signal.emit(pct, msg)

                self.progress_signal.emit(65, f"Etap 3/3: Rozpoznawanie osób i łączenie z tekstem{speaker_info}...")
                final_html, final_plain, turns = diarizer.process(
                    prepared_wav_path,
                    transcript_words,
                    batch_size=32,
                    num_speakers=self.num_speakers,
                    min_speakers=self.min_speakers,
                    max_speakers=self.max_speakers,
                    progress_callback=on_diar_progress
                )

                # Aktualizacja pliku TXT o przypisanych mówców
                try:
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(final_plain)
                    print(f"💾 [AUTOZAPIS] Zaktualizowano plik z mówcami: {txt_path}")
                except Exception as update_err:
                    print(f"⚠️ [AUTOZAPIS] Błąd aktualizacji TXT: {update_err}")

            else:
                final_html = init_html
                final_plain = init_plain
                turns = init_turns

            print("="*70)
            print(f"🎉 [PLIK] Sukces! Przetwarzanie zakończone: {os.path.basename(prepared_wav_path)}")
            print("="*70 + "\n")

            self.progress_signal.emit(100, "Przetwarzanie zakończone pomyślnie!")
            self.finished_signal.emit(final_html, final_plain, prepared_wav_path, turns)

        except Exception as e:
            print(f"❌ [BŁĄD PRZETWARZANIA]: {e}", file=sys.stderr)
            self.error_signal.emit(str(e))


class DiarizationOnlyWorker(QThread):
    """
    Dedykowany wątek do asynchronicznego uruchamiania diaryzacji PyAnnote na istniejącym pliku audio i sesji JSON.
    Nie uruchamia Whispera – wykorzystuje gotowe słowa z zapisanego pliku sesji!
    """
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(str, str, list, str)  # (final_html, final_plain, turns, session_path)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        audio_path: str,
        transcript_words: List[Dict[str, Any]],
        hf_token: str,
        session_json_path: Optional[str] = None,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None
    ):
        super().__init__()
        self.audio_path = audio_path
        self.transcript_words = transcript_words
        self.hf_token = hf_token
        self.session_json_path = session_json_path
        self.num_speakers = num_speakers
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers

    def run(self):
        try:
            if not os.path.exists(self.audio_path):
                self.error_signal.emit("Plik audio dla tej sesji nie istnieje na dysku.")
                return

            if not self.transcript_words:
                self.error_signal.emit("Brak słów transkrypcji w sesji. Najpierw wykonaj transkrypcję.")
                return

            self.progress_signal.emit(5, "Inicjalizacja modułu diaryzacji PyAnnote...")
            from recorder.core.diarizer import DiarizationEngine
            from recorder.core.session import TranscriptionSession, get_session_path_for_audio, extract_datetime_from_filename
            from recorder.config import TRANSCRIPTIONS_DIR

            speaker_info = ""
            if self.num_speakers:
                speaker_info = f" (dokładnie {self.num_speakers} os.)"
            elif self.min_speakers and self.max_speakers:
                speaker_info = f" ({self.min_speakers}-{self.max_speakers} os.)"
            elif self.max_speakers:
                speaker_info = f" (max {self.max_speakers} os.)"

            self.progress_signal.emit(15, f"Ładowanie modelu PyAnnote{speaker_info}...")
            diarizer = DiarizationEngine(hf_token=self.hf_token.strip())

            def on_diar_progress(pct: int, msg: str):
                self.progress_signal.emit(pct, msg)

            json_path = self.session_json_path or get_session_path_for_audio(self.audio_path, TRANSCRIPTIONS_DIR)
            session = TranscriptionSession.load_from_json(json_path) if os.path.exists(json_path) else None

            session_dt = None
            if session and session.created_at:
                try:
                    session_dt = datetime.fromisoformat(session.created_at)
                except Exception:
                    pass
            if not session_dt:
                session_dt = extract_datetime_from_filename(self.audio_path)

            self.progress_signal.emit(30, f"Rozpoznawanie osób i łączenie z gotowym tekstem{speaker_info}...")
            final_html, final_plain, turns = diarizer.process(
                self.audio_path,
                self.transcript_words,
                num_speakers=self.num_speakers,
                min_speakers=self.min_speakers,
                max_speakers=self.max_speakers,
                session_start_time=session_dt,
                progress_callback=on_diar_progress
            )

            # Aktualizacja pliku sesji JSON
            if session:
                session.has_diarization = True
                session.turns = turns
                session.speakers_detected = sorted(list(set(t.get("speaker") for t in turns if t.get("speaker"))))
                session.save_to_json(json_path)
                final_plain = session.export_to_plain_text(session_start_time=session_dt)
                final_html = session.export_to_html(session_start_time=session_dt)

            self.progress_signal.emit(100, "Diaryzacja zakończona pomyślnie!")
            self.finished_signal.emit(final_html, final_plain, turns, json_path or "")

        except Exception as e:
            print(f"❌ [BŁĄD DIARYZACJI]: {e}", file=sys.stderr)
            self.error_signal.emit(str(e))

