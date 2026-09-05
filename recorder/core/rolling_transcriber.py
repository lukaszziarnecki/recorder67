import os
import sys
import time
import queue
import uuid
import numpy as np
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from PySide6.QtCore import QThread, Signal as pyqtSignal

from recorder.core.transcriber import TranscriberEngine, is_hallucination, clean_repeated_text, filter_repeated_words_list
from recorder.core.diarizer import format_transcript_without_diarization
from recorder.core.speakers import format_turns, suggest_speaker_names
from recorder.config import (
    DEFAULT_WHISPER_MODEL,
    DEFAULT_BEAM_SIZE,
    DEFAULT_INITIAL_PROMPT,
    get_full_initial_prompt,
    get_beam_size,
    is_adaptive_beam_size,
    get_theme,
    get_speaker_colors,
)

from recorder.audio.converter import highpass_filter_audio, normalize_audio


class RollingBlock:
    """
    Struktura reprezentująca pojedynczy zamknięty blok nagrania audio w sesji.
    Obsługuje oznaczenie źródła kanału (np. 'mic' = mikrofon biurowy, 'system' = dźwięk Discord/Teams).
    """
    def __init__(self, block_index: int, start_sec: float, end_sec: float, audio_float: np.ndarray, channel_source: str = "mic"):
        self.block_index = block_index
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.audio_float = audio_float
        self.channel_source = channel_source  # 'mic' lub 'system'
        self.turns: List[Dict[str, Any]] = []
        self.words: List[Dict[str, Any]] = []
        self.plain_text: str = ""
        self.html_text: str = ""
        self.is_processed: bool = False
        from datetime import datetime, timedelta
        now_dt = datetime.now()
        dur = (len(audio_float) / 16000.0) if audio_float is not None and len(audio_float) > 0 else max(0.0, float(end_sec - start_sec))
        self.wall_end_time = now_dt
        self.wall_start_time = now_dt - timedelta(seconds=max(0.0, float(dur)))


class RollingTranscriptionWorker(QThread):
    """
    Wątek asynchronicznego przetwarzania bloków transkrypcji w tle (Rolling Background Transcriber).
    Przelicza czasy lokalne słów na globalną oś czasu spotkania i na bieżąco aktualizuje transkrypcję.
    """
    block_processed_signal = pyqtSignal(int, float, float, list, str, str)  # (block_idx, processed_sec, total_sec, all_turns, full_plain, full_html)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str, str, list)  # (final_html, final_plain, all_turns)
    error_signal = pyqtSignal(str)

    def __init__(self, model_size: str = DEFAULT_WHISPER_MODEL, txt_save_path: Optional[str] = None,
                 session_start_time: Optional[Union[datetime, float, int]] = None):
        super().__init__()
        self.model_size = model_size
        self.txt_save_path = txt_save_path
        if isinstance(session_start_time, (int, float)):
            try:
                session_start_time = datetime.fromtimestamp(session_start_time)
            except Exception:
                session_start_time = None
        self.session_start_time = session_start_time  # Realna godzina startu sesji (do timestampów z godziną)
        self.block_queue: queue.Queue = queue.Queue()
        self._is_running: bool = False
        self.transcriber = TranscriberEngine(model_size=self.model_size)
        
        self.processed_blocks: List[RollingBlock] = []
        self.all_turns: List[Dict[str, Any]] = []
        self._all_words: List[Dict[str, Any]] = []
        self.total_processed_seconds: float = 0.0
        self.latest_session_seconds: float = 0.0
        self._last_disk_save_time: float = 0.0
        self._last_ui_render_time: float = 0.0
        self._cached_html: str = ""
        self._cached_plain: str = ""
        self._cached_session: Optional[Any] = None

    @property
    def completed_blocks(self) -> List[Dict[str, Any]]:
        """Alias dla all_turns zapewniający pełną kompatybilność wsteczną."""
        return self.all_turns

    @completed_blocks.setter
    def completed_blocks(self, value: List[Dict[str, Any]]):
        self.all_turns = list(value or [])

    def add_block(self, block_index: int, start_sec: float, end_sec: float, audio_float: np.ndarray, channel_source: str = "mic"):
        """Dodaje nowy zamknięty blok audio do kolejki przetwarzania w tle z oznaczeniem źródła (mic / system)."""
        if self._is_running:
            block = RollingBlock(block_index, start_sec, end_sec, audio_float, channel_source=channel_source)
            from datetime import timedelta
            now_dt = datetime.now()
            block.wall_end_time = now_dt
            dur = (len(audio_float) / 16000.0) if audio_float is not None and len(audio_float) > 0 else (end_sec - start_sec)
            block.wall_start_time = now_dt - timedelta(seconds=max(0.0, float(dur)))
            self.latest_session_seconds = max(self.latest_session_seconds, end_sec)
            self.block_queue.put(block)

    def reset_for_new_session(self, new_txt_save_path: Optional[str] = None,
                              session_start_time: Optional[Union[datetime, float, int]] = None):
        """Resetuje stan przetworzonych bloków dla nowej sesji spotkania bez konieczności ponownego ładowania modelu Whisper."""
        self.txt_save_path = new_txt_save_path
        if session_start_time is not None:
            if isinstance(session_start_time, (int, float)):
                try:
                    session_start_time = datetime.fromtimestamp(session_start_time)
                except Exception:
                    session_start_time = None
            self.session_start_time = session_start_time
        self.processed_blocks = []
        self.all_turns = []
        self._all_words = []
        self.total_processed_seconds = 0.0
        self.latest_session_seconds = 0.0
        self._last_disk_save_time = 0.0
        self._last_ui_render_time = 0.0
        self._cached_html = ""
        self._cached_plain = ""
        self._cached_session = None

    def update_session_time(self, current_sec: float):
        """Aktualizuje bieżący czas sesji ze stopera."""
        self.latest_session_seconds = max(self.latest_session_seconds, float(current_sec))

    def stop_and_finalize(self, final_block: Optional[RollingBlock] = None):
        """Zgłasza zakończenie nagrywania i zamyka kolejkę po przetworzeniu ewentualnego ostatniego bloku."""
        self._is_running = False
        if final_block:
            self.block_queue.put(final_block)
        self.block_queue.put(None)

    def stop(self):
        """Natychmiast przerywa pętlę roboczą i odblokowuje wątek."""
        self._is_running = False
        while not self.block_queue.empty():
            try:
                self.block_queue.get_nowait()
                self.block_queue.task_done()
            except Exception:
                break
        self.block_queue.put(None)

    def get_all_words(self) -> List[Dict[str, Any]]:
        """Zwraca wszystkie przetranskrybowane słowa ze znacznikami czasu ze wszystkich bloków sesji."""
        if hasattr(self, "_all_words") and self._all_words:
            return list(self._all_words)
        all_words = []
        for b in sorted(self.processed_blocks, key=lambda x: x.start_sec):
            if hasattr(b, "words") and b.words:
                all_words.extend(b.words)
        return all_words

    def run(self):
        self._is_running = True
        try:
            self.status_signal.emit(f"Inicjalizacja silnika Whisper ({self.model_size})...")
            self.transcriber.load_model()
            self.status_signal.emit("Silnik transkrypcji w tle: GOTOWY")

            while self._is_running or not self.block_queue.empty():
                if self.block_queue.empty():
                    self.msleep(100)
                    continue

                try:
                    block: Optional[RollingBlock] = self.block_queue.get_nowait()
                except Exception:
                    continue

                if block is None:
                    break

                # 1. Przetworzenie bloku audio
                self._process_single_block(block)
                self.block_queue.task_done()

            # 2. Finalizacja całego spotkania po zakończeniu kolejki
            final_html, final_plain, turns = self._compile_full_transcript()
            self._save_to_txt_file(final_plain)
            self._save_to_session_file(turns, force=True)
            self.finished_signal.emit(final_html, final_plain, turns)

        except Exception as e:
            self.error_signal.emit(f"Błąd przetwarzania w tle: {e}")

    def _process_single_block(self, block: RollingBlock):
        """Transkrybuje pojedynczy blok i mapuje jego słowa na globalną oś czasu."""
        audio_data = block.audio_float
        if audio_data is None or len(audio_data) < int(1.0 * 16000):
            block.audio_float = None
            block.is_processed = True
            self.processed_blocks.append(block)
            self.total_processed_seconds = max(self.total_processed_seconds, block.end_sec)
            self.block_processed_signal.emit(
                block.block_index,
                self.total_processed_seconds,
                max(self.latest_session_seconds, self.total_processed_seconds),
                self.all_turns,
                self._cached_plain,
                ""
            )
            return

        # Dźwięk w formacie float32 mono 16kHz (zawsze 1D)
        if audio_data.dtype == np.int16:
            audio_float = (audio_data.astype(np.float32) / 32768.0).flatten()
        else:
            audio_float = audio_data.astype(np.float32).flatten()

        if len(audio_float) < int(1.0 * 16000):
            block.audio_float = None
            block.is_processed = True
            self.processed_blocks.append(block)
            self.total_processed_seconds = max(self.total_processed_seconds, block.end_sec)
            self.block_processed_signal.emit(
                block.block_index,
                self.total_processed_seconds,
                max(self.latest_session_seconds, self.total_processed_seconds),
                self.all_turns,
                self._cached_plain,
                ""
            )
            return

        # Oczyszczenie pasma i normalizacja głośności bloku
        audio_clean = highpass_filter_audio(audio_float, sr=16000, cutoff_hz=80.0)
        audio_norm = normalize_audio(audio_clean, target_peak=0.92)

        initial_prompt = get_full_initial_prompt()
        base_beam = get_beam_size()
        allow_adaptive = is_adaptive_beam_size()
        q_len = self.block_queue.qsize()
        if allow_adaptive and q_len > 1:
            effective_beam = 1
            if not getattr(self, "_was_adaptive_beam", False):
                print(f"[WHISPER ADAPTACYJNY] Aktywacja biegu turbo: w kolejce czeka {q_len} bloków -> nadrabianie (beam_size=1)")
                self._was_adaptive_beam = True
            else:
                print(f"[WHISPER ADAPTACYJNY] Bieg turbo: w kolejce pozostało {q_len} bloków -> beam_size=1")
        else:
            effective_beam = base_beam
            if getattr(self, "_was_adaptive_beam", False):
                print(f"[WHISPER ADAPTACYJNY] Kolejka rozładowana -> powrót do pełnej jakości: beam_size={effective_beam}")
                self._was_adaptive_beam = False

        transcript_words = []

        try:
            # Transkrypcja bloku z word-level timestamps i beam search
            segments, _ = self.transcriber._model.transcribe(
                audio_norm,
                word_timestamps=True,
                language="pl",
                beam_size=effective_beam,
                temperature=0.0,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4,
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.35,
                    min_speech_duration_ms=200,
                    min_silence_duration_ms=400,
                    speech_pad_ms=400
                ),
                initial_prompt=initial_prompt
            )

            for segment in segments:
                raw_text = segment.text.strip() if segment.text else ""
                seg_text = clean_repeated_text(raw_text)

                if not seg_text or is_hallucination(raw_text, seg_text):
                    continue

                # Jeśli segment ma dokładne słowa
                if segment.words:
                    valid_words = [
                        w for w in segment.words
                        if w.word and w.start is not None and w.end is not None
                    ]
                    filtered_valid = filter_repeated_words_list(valid_words, max_consecutive=2)
                    for w in filtered_valid:
                        w_text = w.word if hasattr(w, "word") else w.get("word", "")
                        w_start = float(w.start if hasattr(w, "start") else w.get("start", 0.0))
                        w_end = float(w.end if hasattr(w, "end") else w.get("end", 0.0))
                        prob = float(getattr(w, "probability", 1.0)) if hasattr(w, "probability") else float(w.get("probability", 1.0))

                        # Globalne timestampy: start_sec całego bloku + lokalny start słowa
                        g_start = round(block.start_sec + w_start, 2)
                        g_end = round(block.start_sec + w_end, 2)
                        transcript_words.append({
                            "word": w_text,
                            "start": g_start,
                            "end": g_end,
                            "probability": prob,
                            "channel": block.channel_source
                        })
                else:
                    # Fallback estymacji słów
                    words = seg_text.split()
                    if words:
                        w_dur = (segment.end - segment.start) / max(1, len(words))
                        for i, w_str in enumerate(words):
                            w_start = round(block.start_sec + segment.start + (i * w_dur), 2)
                            w_end = round(w_start + w_dur, 2)
                            transcript_words.append({
                                "word": (" " + w_str if i > 0 else w_str),
                                "start": w_start,
                                "end": w_end,
                                "probability": 0.9,
                                "channel": block.channel_source
                            })
        except Exception as trans_err:
            print(f"[ROLLING] Pominięto fragment bloku #{block.block_index}: {trans_err}")

        # Formatowanie słów tego bloku do turnów
        block.words = transcript_words
        if transcript_words:
            _, _, block_turns = format_transcript_without_diarization(transcript_words)
            default_spk = "Mikrofon" if block.channel_source == "mic" else "Dźwięk Systemu"
            from datetime import timedelta
            b_wall_st = getattr(block, "wall_start_time", None)
            if b_wall_st is None:
                if self.session_start_time is not None:
                    b_wall_st = self.session_start_time + timedelta(seconds=block.start_sec)
                else:
                    dur = max(0.0, block.end_sec - block.start_sec)
                    b_wall_st = datetime.now() - timedelta(seconds=dur)

            for trn in block_turns:
                if not trn.get("id"):
                    trn["id"] = str(uuid.uuid4())
                trn["channel"] = block.channel_source
                if trn.get("speaker") in ("Mówca", None, "", "Ty / Biuro", "Zdalny (Discord/Teams)"):
                    trn["speaker"] = default_spk
                rel_st = max(0.0, float(trn.get("start", 0.0)) - float(block.start_sec))
                rel_en = max(0.0, float(trn.get("end", 0.0)) - float(block.start_sec))
                trn["wall_start"] = (b_wall_st + timedelta(seconds=rel_st)).isoformat()
                trn["wall_end"] = (b_wall_st + timedelta(seconds=rel_en)).isoformat()
            block.turns = block_turns

        # ZWALNIANIE PAMIĘCI RAM: usuwamy referencję do surowych danych audio, których już nie potrzebujemy
        block.audio_float = None

        block.is_processed = True
        self.processed_blocks.append(block)
        if transcript_words:
            self._all_words.extend(transcript_words)
        self.total_processed_seconds = max(self.total_processed_seconds, block.end_sec)

        # Inkrementalne dopisanie nowych turnów do self.all_turns (O(1) dla chronologicznych bloków)
        if block.turns:
            from recorder.core.session import turn_sort_key
            self.all_turns.extend(block.turns)
            self.all_turns.sort(key=lambda t: turn_sort_key(t, self.session_start_time))

        qsize = self.block_queue.qsize()
        now_ts = time.time()
        is_queue_empty = (qsize == 0)

        # Tryb Catch-up i buforowanie renderowania UI:
        # Jeśli w kolejce czeka wiele bloków, nie zamrażamy interfejsu i CPU renderowaniem wielomegabajtowego HTML.
        should_render_ui = (is_queue_empty and (now_ts - self._last_ui_render_time >= 1.5)) or (now_ts - self._last_ui_render_time >= 3.0) or not self._cached_html
        if should_render_ui:
            self._last_ui_render_time = now_ts
            full_html, full_plain, all_turns = self._compile_full_transcript()
            self._cached_html = full_html
            self._cached_plain = full_plain
        else:
            full_html = ""  # Sygnał dla UI: zaktualizuj pasek postępu bez kosztownego re-renderingu QTextEdit
            full_plain = self._cached_plain
            all_turns = self.all_turns

        # Zapis dyskowy throttled (co min. 30s)
        should_save_disk = (now_ts - self._last_disk_save_time >= 30.0) and bool(self.all_turns)
        if should_save_disk:
            if not should_render_ui:
                self._cached_html, self._cached_plain, _ = self._compile_full_transcript()
                self._last_ui_render_time = now_ts
            self._last_disk_save_time = now_ts
            self._save_to_txt_file(self._cached_plain)
            self._save_to_session_file(self.all_turns, force=True)

        # Emitowanie sygnału aktualizacji do UI
        self.block_processed_signal.emit(
            block.block_index,
            self.total_processed_seconds,
            max(self.latest_session_seconds, self.total_processed_seconds),
            all_turns,
            full_plain,
            full_html
        )

    def _compile_full_transcript(self, theme_id: Optional[str] = None):
        """Kompiluje dotychczasowe wypowiedzi w spójną transkrypcję posortowaną chronologicznie z kolorami motywu."""
        from recorder.core.session import turn_sort_key
        combined_turns = sorted(list(self.all_turns), key=lambda t: turn_sort_key(t, self.session_start_time))
        if not combined_turns:
            for b in sorted(self.processed_blocks, key=lambda x: x.start_sec):
                combined_turns.extend(b.turns)
            combined_turns.sort(key=lambda t: turn_sort_key(t, self.session_start_time))

        if not combined_turns:
            return "Brak zarejestrowanej mowy.", "Brak zarejestrowanej mowy.", []

        from recorder.core.session import format_turn_timestamp
        from recorder.config import get_preview_order, get_timestamp_format

        reverse_order = (get_preview_order() == "newest_first")
        ts_format = get_timestamp_format()

        # O(1) pobranie mapowania kolorów mówców dla aktywnego motywu
        active_theme = theme_id if theme_id is not None else get_theme()
        spk_colors = get_speaker_colors(active_theme)
        mic_color = spk_colors.get("mic", "#4cc9f0")
        sys_color = spk_colors.get("system", "#a370f7")

        plain_parts = []
        for t in combined_turns:
            spk = t.get("speaker", "Mówca")
            channel = t.get("channel", "mic")
            st = float(t.get("start", 0.0))
            en = float(t.get("end", 0.0))
            txt = t.get("text", "")

            time_label = format_turn_timestamp(st, en, self.session_start_time, ts_format=ts_format, wall_start=t.get("wall_start"), wall_end=t.get("wall_end"))
            badge = "🎧 " if channel == "system" else "🎙️ "
            display_spk = f"{badge}{spk}" if not (spk.startswith("🎙️") or spk.startswith("🎧")) else spk
            plain_parts.append(f"[{time_label}] {display_spk}: {txt}\n\n")
        full_plain = "".join(plain_parts)

        html_parts = []
        display_turns = list(reversed(combined_turns)) if reverse_order else combined_turns
        for t in display_turns:
            spk = t.get("speaker", "Mówca")
            channel = t.get("channel", "mic")
            st = float(t.get("start", 0.0))
            en = float(t.get("end", 0.0))
            txt = t.get("text", "")

            time_label = format_turn_timestamp(st, en, self.session_start_time, ts_format=ts_format, wall_start=t.get("wall_start"), wall_end=t.get("wall_end"))
            if channel == "system":
                badge = "🎧 "
                color = sys_color
            else:
                badge = "🎙️ "
                color = mic_color

            display_spk = f"{badge}{spk}" if not (spk.startswith("🎙️") or spk.startswith("🎧")) else spk
            html_parts.append(f"<b>[{time_label}] <span style='color: {color};'>{display_spk}:</span></b> {txt}<br><br>")
        full_html = "".join(html_parts)

        return full_html, full_plain, combined_turns


    def _save_to_txt_file(self, content: str):
        """Bezpiecznie zapisuje bieżącą transkrypcję do pliku TXT na dysku."""
        if not self.txt_save_path or not content:
            return
        try:
            with open(self.txt_save_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
        except Exception as e:
            import logging
            logging.getLogger("recorder").warning(f"Błąd zapisu transkrypcji do pliku TXT '{self.txt_save_path}': {e}")

    def _save_to_session_file(self, turns: list, force: bool = False):
        """Automatycznie tworzy i zapisuje plik sesji JSON z kompletem słów z Whispera."""
        if not self.txt_save_path:
            return
        now_ts = time.time()
        if not force and (now_ts - self._last_disk_save_time < 30.0):
            return
        json_path = "nieznana_ścieżka"
        try:
            from recorder.core.session import TranscriptionSession, get_session_path_for_txt
            json_path = get_session_path_for_txt(self.txt_save_path)

            all_words = self.get_all_words()

            if not hasattr(self, "_cached_session") or self._cached_session is None:
                self._cached_session = TranscriptionSession.load_from_json(json_path) or TranscriptionSession()

            session = self._cached_session
            session.has_transcription = True
            session.whisper_model = self.model_size
            session.duration_sec = max(self.latest_session_seconds, self.total_processed_seconds)
            session.turns = list(turns or [])
            session.words = list(all_words or [])
            session.save_to_json(json_path)
            self._last_disk_save_time = now_ts
        except Exception as e:
            import logging
            logging.getLogger("recorder").warning(f"Błąd zapisu pliku sesji JSON '{json_path}': {e}")


# Alias dla zachowania pełnej kompatybilności wstecznej i testów E2E
RollingTranscriber = RollingTranscriptionWorker

