import os
import sys
import time
import wave
import tempfile
import threading
import numpy as np
from unittest.mock import MagicMock

# Ustawienie ścieżki do projektu
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtWidgets import QApplication
from recorder.audio.capture import StreamingWavWriter
from recorder.ui.workers import SmartAudioWorker, RealtimeAudioMixer
from recorder.core.rolling_transcriber import RollingBlock, RollingTranscriptionWorker
from recorder.core.session import TranscriptionSession
from recorder.core.vad import SileroVADDetector


def test_smart_audio_worker_lifecycle_and_save_wav():
    """
    Weryfikuje poprawność cyklu życia SmartAudioWorker:
    stop_recording() zamyka plik strumieniowy na dysku, a późniejsze wywołanie save_wav(path)
    zwraca True i zachowuje prawidłowy plik WAV o rozmiarze > 44 bajtów.
    """
    _ = QApplication.instance() or QApplication([])

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        save_path = tmp.name

    try:
        worker = SmartAudioWorker()
        # Inicjalizacja StreamingWavWriter symulująca start nagrywania
        worker.wav_writer = StreamingWavWriter(save_path, channels=1, samplerate=16000)

        # Zapis próbki audio (1 sekunda 16kHz PCM)
        dummy_pcm = (np.ones(16000, dtype=np.int16) * 500).tobytes()
        worker.wav_writer.write_frames(dummy_pcm)

        # 1. Zatrzymanie nagrywania - zamyka wav_writer i ustawia go na None
        worker.stop_recording()
        assert worker.wav_writer is None, "Po stop_recording wav_writer powinien być None"

        # 2. Wywołanie save_wav(path) - nie może nadpisać pliku pustą listą frames ani zwrócić False
        saved = worker.save_wav(save_path)
        assert saved is True, "save_wav() powinno zwrócić True dla zapisanego strumieniowo pliku"
        assert os.path.exists(save_path), "Plik WAV musi istnieć na dysku"
        file_size = os.path.getsize(save_path)
        assert file_size > 44, f"Rozmiar pliku WAV ({file_size} B) musi być większy niż nagłówek (44 B)"

        with wave.open(save_path, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 16000

        # 3. Dodatkowy test ścieżki bezpośredniego zapisu, gdy wav_writer jest jeszcze otwarty
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp2:
            save_path2 = tmp2.name
        try:
            worker2 = SmartAudioWorker()
            worker2.wav_writer = StreamingWavWriter(save_path2, channels=1, samplerate=16000)
            worker2.wav_writer.write_frames(dummy_pcm)
            saved2 = worker2.save_wav(save_path2)
            assert saved2 is True
            assert os.path.exists(save_path2)
            assert os.path.getsize(save_path2) > 44
            assert worker2.wav_writer is None
        finally:
            if os.path.exists(save_path2):
                try:
                    os.remove(save_path2)
                except Exception:
                    pass

        # 4. Test zapisu do nowej lokalizacji docelowej (innej niż save_wav_path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_streamed:
            streamed_path = tmp_streamed.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_target:
            target_path = tmp_target.name
        try:
            worker3 = SmartAudioWorker()
            worker3.save_wav_path = streamed_path
            worker3.wav_writer = StreamingWavWriter(streamed_path, channels=1, samplerate=16000)
            worker3.wav_writer.write_frames(dummy_pcm)
            worker3.stop_recording()

            # Usunięcie pliku docelowego, aby zasymulować nową ścieżkę eksportu
            if os.path.exists(target_path):
                os.remove(target_path)

            saved3 = worker3.save_wav(target_path)
            assert saved3 is True
            assert os.path.exists(target_path)
            assert os.path.getsize(target_path) > 44
        finally:
            for p in (streamed_path, target_path):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    finally:
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass


def test_audio_mixer_8h_asymmetry_and_memory():
    """
    Symuluje 5000 asymetrycznych kroków dodawania ramek do miksera audio (mikrofon + loopback).
    Weryfikuje, że bufory nigdy nie przekraczają 8000 próbek i pamięć RAM jest ściśle ograniczona O(1).
    """
    mixer = RealtimeAudioMixer()

    # Symulacja 5000 asynchronicznych fragmentów z dryfem zegarów i asymetrią pakietów
    for i in range(5000):
        # Mikrofon wysyła pakiety regularnie (1024 próbki)
        mic_chunk = (np.sin(np.linspace(0, 10, 1024)) * 0.4).astype(np.float32)
        mixer.add_mic_chunk(mic_chunk)

        # Dźwięk systemowy ma asymetrię (pakiety o różnej wielkości lub sporadyczne opóźnienia)
        if i % 2 == 0:
            sys_chunk = (np.cos(np.linspace(0, 10, 600)) * 0.3).astype(np.float32)
            mixer.add_sys_chunk(sys_chunk)

        # Pętla robocza SmartAudioWorker opróżnia mikser co interwał
        _ = mixer.pop_mixed_frames(is_hybrid=True, run_mic=True, run_sys=True)

        assert len(mixer.mic_buffer) <= 8000, f"mic_buffer={len(mixer.mic_buffer)} przekroczył limit 8000 próbek w kroku {i}"
        assert len(mixer.sys_buffer) <= 8000, f"sys_buffer={len(mixer.sys_buffer)} przekroczył limit 8000 próbek w kroku {i}"

    # Ekstremalny test: całkowita cisza na kanale systemu przez 1000 iteracji
    for i in range(1000):
        mic_chunk = np.ones(1024, dtype=np.float32) * 0.1
        mixer.add_mic_chunk(mic_chunk)
        _ = mixer.pop_mixed_frames(is_hybrid=True, run_mic=True, run_sys=True)

        assert len(mixer.mic_buffer) <= 8000, f"mic_buffer={len(mixer.mic_buffer)} przekroczył limit przy braku loopback"
        assert len(mixer.sys_buffer) <= 8000, f"sys_buffer={len(mixer.sys_buffer)} przekroczył limit przy braku loopback"

    assert len(mixer.mic_buffer) <= 8000
    assert len(mixer.sys_buffer) <= 8000


def test_rolling_worker_disk_throttling_8h_simulation():
    """
    Symuluje 3000 bloków mowy (odpowiednik sesji biurowej ~8.3h, 3000 x 10s).
    Weryfikuje, że throttling zapisu dyskowego (30s) ogranicza liczbę zapisów pośrednich (<= 2)
    oraz że stop_and_finalize zapisuje 100% wszystkich wypowiedzi do plików TXT i JSON.
    """
    _ = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as tmp_dir:
        txt_path = os.path.join(tmp_dir, "sesja_biurowa_8h.txt")
        worker = RollingTranscriptionWorker(txt_save_path=txt_path)

        disk_writes = {"txt": 0, "session": 0}
        orig_save_txt = worker._save_to_txt_file
        orig_save_session = worker._save_to_session_file

        def tracked_save_txt(content):
            disk_writes["txt"] += 1
            return orig_save_txt(content)

        def tracked_save_session(turns, force=False):
            disk_writes["session"] += 1
            return orig_save_session(turns, force=force)

        worker._save_to_txt_file = tracked_save_txt
        worker._save_to_session_file = tracked_save_session

        # Szybka symulacja 3000 bloków mowy
        for i in range(3000):
            block = RollingBlock(
                block_index=i,
                start_sec=float(i * 10),
                end_sec=float((i + 1) * 10),
                audio_float=None,
                channel_source="mic" if i % 2 == 0 else "system"
            )
            block.turns = [{
                "speaker": "Mikrofon" if i % 2 == 0 else "Dźwięk Systemu",
                "start": float(i * 10),
                "end": float((i + 1) * 10),
                "text": f"Kluczowe ustalenie spotkania w bloku #{i}",
                "channel": block.channel_source
            }]

            worker.processed_blocks.append(block)
            worker.all_turns.extend(block.turns)
            worker.total_processed_seconds = block.end_sec

            now_ts = time.time()
            # Logika buforowania UI
            should_render_ui = (now_ts - worker._last_ui_render_time >= 3.0) or not worker._cached_html
            if should_render_ui:
                worker._last_ui_render_time = now_ts
                h, p, _ = worker._compile_full_transcript()
                worker._cached_html = h
                worker._cached_plain = p

            # Logika throttlingu dyskowego (co 30s bez uzależnienia od is_queue_empty)
            should_save_disk = (now_ts - worker._last_disk_save_time >= 30.0) and bool(worker._cached_plain)
            if should_save_disk:
                worker._last_disk_save_time = now_ts
                worker._save_to_txt_file(worker._cached_plain)
                worker._save_to_session_file(worker.all_turns, force=True)

        # Podczas szybkiej symulacji (wykonanie w ułamku sekundy) liczba zapisów pośrednich musi wynosić <= 2
        assert disk_writes["txt"] <= 2, f"Oczekiwano <= 2 zapisów TXT podczas szybkiej symulacji, otrzymano {disk_writes['txt']}"
        assert disk_writes["session"] <= 2, f"Oczekiwano <= 2 zapisów sesji podczas szybkiej symulacji, otrzymano {disk_writes['session']}"

        # Finalizacja nagrania (jak na końcu run() po wywołaniu stop_and_finalize)
        final_html, final_plain, turns = worker._compile_full_transcript()
        worker._save_to_txt_file(final_plain)
        worker._save_to_session_file(turns, force=True)

        # Sprawdzenie integralności pliku tekstowego TXT
        assert os.path.exists(txt_path)
        with open(txt_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "Kluczowe ustalenie spotkania w bloku #0" in content
            assert "Kluczowe ustalenie spotkania w bloku #2999" in content

        # Sprawdzenie integralności pliku sesji JSON
        from recorder.core.session import get_session_path_for_txt
        json_path = get_session_path_for_txt(txt_path)
        assert os.path.exists(json_path)
        session = TranscriptionSession.load_from_json(json_path)
        assert session is not None
        assert len(session.turns) == 3000
        assert session.duration_sec == 30000.0


def test_vad_inference_mode_thread_safety():
    """
    Weryfikuje współbieżną ocenę fragmentów audio przez model Silero VAD w wielu wątkach
    z użyciem torch.inference_mode() i wewnętrznego rygla wątkowego _silero_lock.
    """
    errors = []

    def vad_worker(thread_id: int):
        detector = SileroVADDetector()
        try:
            for _ in range(80):
                chunk = (np.random.randn(512) * 0.05).astype(np.float32)
                is_speech, prob = detector.process_chunk(chunk, samplerate=16000)
                assert isinstance(is_speech, (bool, np.bool_))
                assert isinstance(prob, (float, np.floating))
                assert 0.0 <= prob <= 1.0
        except Exception as e:
            errors.append((thread_id, e))

    threads = [threading.Thread(target=vad_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Wykryto błędy wielowątkowości w VAD: {errors}"


def test_adaptive_beam_size_under_load():
    """
    Weryfikuje, że w sytuacji nagromadzenia bloków w kolejce (qsize > 1),
    algorytm automatycznie redukuje beam_size do 1 (greedy decoding),
    aby uniknąć przegrzewania procesora i natychmiast dogonić czas rzeczywisty.
    """
    _ = QApplication.instance() or QApplication([])
    worker = RollingTranscriptionWorker()

    # Zamockowanie silnika Whisper, aby nie ładować ciężkich wag w teście jednostkowym
    captured_beam_sizes = []

    def mock_transcribe(audio_norm, **kwargs):
        captured_beam_sizes.append(kwargs.get("beam_size"))
        return [], None

    worker.transcriber = MagicMock()
    worker.transcriber._model.transcribe = mock_transcribe

    from unittest.mock import patch
    with patch("recorder.core.rolling_transcriber.is_adaptive_beam_size", return_value=True):
        # 1. Kolejka z 1 blokiem lub pusta -> normalny beam_size (domyślnie > 1 z config)
        dummy_audio = np.zeros(16000 * 2, dtype=np.float32)
        block_normal = RollingBlock(1, 0.0, 2.0, dummy_audio)
        worker._process_single_block(block_normal)
        assert len(captured_beam_sizes) == 1
        assert captured_beam_sizes[0] > 1, f"Oczekiwano domyślnego beam_size > 1, otrzymano {captured_beam_sizes[0]}"

        # 2. Kolejka z > 1 blokami (spiętrzenie w kolejce pod obciążeniem)
        captured_beam_sizes.clear()
        b_pending1 = RollingBlock(2, 2.0, 4.0, dummy_audio)
        b_pending2 = RollingBlock(3, 4.0, 6.0, dummy_audio)
        worker.block_queue.put(b_pending1)
        worker.block_queue.put(b_pending2)
        assert worker.block_queue.qsize() == 2

        block_under_load = RollingBlock(4, 6.0, 8.0, dummy_audio)
        worker._process_single_block(block_under_load)

        assert len(captured_beam_sizes) == 1
        assert captured_beam_sizes[0] == 1, f"Oczekiwano dynamicznego obniżenia beam_size do 1, otrzymano {captured_beam_sizes[0]}"

        # Opróżnienie kolejki
        while not worker.block_queue.empty():
            worker.block_queue.get_nowait()


def test_short_block_early_return_updates_session_time_and_frees_ram():
    """
    Weryfikuje, że dla bloków krótszych niż 1.0s (np. kaszel, szum, stuknięcie):
    - audio_float jest natychmiast zwalniany (None), aby nie zużywać RAM
    - worker.total_processed_seconds jest poprawnie aktualizowany do block.end_sec
    - block_processed_signal jest emitowany, aby pasek postępu w UI nie zawieszał się
    - zapis sesji JSON poprawnie odnotowuje pełny czas trwania sesji
    """
    _ = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as tmp_dir:
        txt_path = os.path.join(tmp_dir, "test_short_blocks.txt")
        worker = RollingTranscriptionWorker(txt_save_path=txt_path)
        worker.update_session_time(100.0)

        signals_received = []
        worker.block_processed_signal.connect(lambda *args: signals_received.append(args))

        # Blok o długości 0.5s (8000 próbek)
        short_pcm = np.ones(8000, dtype=np.float32) * 0.1
        block_short = RollingBlock(1, start_sec=0.0, end_sec=0.5, audio_float=short_pcm)
        worker._process_single_block(block_short)

        assert block_short.audio_float is None, "audio_float musi być wyczyszczone dla O(1) RAM"
        assert block_short.is_processed is True
        assert worker.total_processed_seconds == 0.5, "total_processed_seconds musi uwzględniać end_sec krótkiego bloku"
        assert len(signals_received) == 1, "block_processed_signal musi być wyemitowany dla aktualizacji paska UI"

        # Zapis sesji: duration_sec musi być max(latest_session_seconds, total_processed_seconds)
        worker._save_to_session_file([], force=True)
        from recorder.core.session import get_session_path_for_txt
        j_path = get_session_path_for_txt(txt_path)
        assert os.path.exists(j_path)
        sess = TranscriptionSession.load_from_json(j_path)
        assert sess.duration_sec == 100.0, f"Oczekiwano duration_sec=100.0, otrzymano {sess.duration_sec}"


def test_rotate_session_file_flushes_audio_mixer():
    """
    Weryfikuje, że rotate_session_file() przed zamknięciem starego pliku StreamingWavWriter
    dokonuje opróżnienia miksera audio (flush remaining bytes), zapobiegając utracie dźwięku
    na przełomie rotacji wielogodzinnych plików.
    """
    _ = QApplication.instance() or QApplication([])

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f1, \
         tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f2:
        path1 = f1.name
        path2 = f2.name

    try:
        worker = SmartAudioWorker()
        worker.start_recording(save_wav_path=path1)

        # Dodanie próbek do miksera (odpowiednik buforowanego dźwięku przed rotacją)
        chunk = (np.ones(1600, dtype=np.float32) * 0.2)
        worker.audio_mixer.add_mic_chunk(chunk)

        # Rotacja do path2
        worker.rotate_session_file(path2)

        # path1 powinien być poprawnie zamknięty i zawierać buforowane próbki
        assert os.path.exists(path1)
        assert os.path.getsize(path1) > 44, "Plik path1 powinien zawierać zrzucone próbki z miksera"
        assert worker.save_wav_path == os.path.abspath(path2)
        assert worker.wav_writer is not None

        worker.stop_recording()
        worker.wait(2000)
    finally:
        for p in (path1, path2):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def test_real_wall_clock_timestamp_with_mute():
    """
    Weryfikuje, że w trybie 'clock_only' (Tylko godzina realna), wypowiedzi po 15 minutach wyciszenia
    lub auto-pauzy otrzymują RZECZYWISTY czas zegarowy ze stopera systemowego (np. 18:15:00),
    a nie czas przesunięty o offset skompresowanego pliku WAV (np. 18:00:05).
    """
    from datetime import datetime, timedelta
    from recorder.core.session import format_turn_timestamp

    t1_wall = datetime(2026, 9, 3, 18, 0, 0)
    t2_wall = datetime(2026, 9, 3, 18, 15, 0)

    # Audio offset: pierwsze zdanie trwa 5s (0.0 do 5.0).
    # Drugie zdanie w skompresowanym WAV ma offset 5.0 do 10.0 (bo 15 minut ciszy nie było nagrywane).
    st1, en1 = 0.0, 5.0
    st2, en2 = 5.0, 10.0

    session_start = datetime(2026, 9, 3, 18, 0, 0)

    lbl1 = format_turn_timestamp(st1, en1, session_start_time=session_start, ts_format="clock_only",
                                 wall_start=t1_wall, wall_end=t1_wall + timedelta(seconds=5.0))
    lbl2 = format_turn_timestamp(st2, en2, session_start_time=session_start, ts_format="clock_only",
                                 wall_start=t2_wall, wall_end=t2_wall + timedelta(seconds=5.0))

    assert lbl1 == "18:00:00 - 18:00:05"
    assert lbl2 == "18:15:00 - 18:15:05", f"Oczekiwano rzeczywistej godziny 18:15:00, otrzymano: {lbl2}"

    # Weryfikacja dla trybu hybrydowego: offset + realna godzina (np. 00:05 - 00:10 | 18:15:00 - 18:15:05)
    lbl1_hybrid = format_turn_timestamp(st1, en1, session_start_time=session_start, ts_format="hybrid",
                                        wall_start=t1_wall, wall_end=t1_wall + timedelta(seconds=5.0))
    lbl2_hybrid = format_turn_timestamp(st2, en2, session_start_time=session_start, ts_format="hybrid",
                                        wall_start=t2_wall, wall_end=t2_wall + timedelta(seconds=5.0))

    assert lbl1_hybrid == "00:00 - 00:05 | 18:00:00 - 18:00:05"
    assert lbl2_hybrid == "00:05 - 00:10 | 18:15:00 - 18:15:05", f"Oczekiwano hybrydy z czasem 18:15:00, otrzymano: {lbl2_hybrid}"


def test_silence_alert_timeout_does_not_cancel_session_split():
    """
    Weryfikuje, że wygaszenie powiadomienia strażnika ciszy (po 5 minutach / 300s)
    NIE kasuje licznika automatycznego podziału sesji (ustawionego na 10 minut / 600s).
    """
    _ = QApplication.instance() or QApplication([])

    worker = SmartAudioWorker()
    worker.set_silence_alert_seconds(300.0)
    worker.set_session_split_silence_sec(600.0)
    worker.session_has_speech = True

    # Symulacja 5 minut (300s) ciszy
    worker.continuous_silence_samples = int(300.0 * 16000)
    worker.session_split_silence_samples = int(300.0 * 16000)

    # Strażnik ciszy zgłasza alert i po 45s braku reakcji resetuje stan alertu
    worker.reset_silence_alert()

    # Licznik alertu powinien być wyzerowany, ale licznik podziału sesji ZACHOWANY!
    assert worker.continuous_silence_samples == 0
    assert worker.session_split_silence_samples == int(300.0 * 16000), \
        "BŁĄD: reset_silence_alert() skasował licznik podziału sesji!"

    # Kolejne 5 minut i 5 sekund ciszy (łącznie > 10 min)
    worker.session_split_silence_samples += int(305.0 * 16000)

    split_events = []
    worker.session_split_signal.connect(lambda r: split_events.append(r))

    # Wywołanie logiki sprawdzania podziału sesji
    split_sil_sec = float(worker.session_split_silence_samples / 16000.0)
    if worker.session_has_speech and worker.session_split_silence_sec > 0:
        if split_sil_sec >= worker.session_split_silence_sec:
            worker.session_has_speech = False
            worker.session_split_silence_samples = 0
            worker.continuous_silence_samples = 0
            mins = int(worker.session_split_silence_sec // 60)
            worker.session_split_signal.emit(f"Cisza > {mins} min")

    assert len(split_events) == 1
    assert "10 min" in split_events[0]


def test_session_save_to_json_with_datetime_objects():
    """
    Weryfikuje, że save_to_json() poprawnie serializuje sesję nawet jeśli
    w wypowiedziach (turns) znajdują się obiekty datetime (wall_start, wall_end),
    nie rzucając błędu TypeError: Object of type datetime is not JSON serializable.
    """
    from datetime import datetime
    import tempfile
    from recorder.core.session import TranscriptionSession

    session = TranscriptionSession()
    session.turns = [
        {
            "start": 0.0,
            "end": 2.5,
            "speaker": "Mikrofon",
            "text": "Dzień dobry.",
            "wall_start": datetime.now(),
            "wall_end": datetime.now()
        }
    ]

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        success = session.save_to_json(tmp_path)
        assert success is True, "save_to_json powinno zwrócić True"
        loaded = TranscriptionSession.load_from_json(tmp_path)
        assert loaded is not None
        assert len(loaded.turns) == 1
        assert isinstance(loaded.turns[0]["wall_start"], str)
        assert "T" in loaded.turns[0]["wall_start"]
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_flush_mic_block_on_mute():
    """
    Weryfikuje, że kliknięcie Mute natychmiast wypycha (flush) zgromadzone próbki mowy,
    dzięki czemu zdania powiedziane tuż przed wyciszeniem nie czekają minutami na odciszenie.
    """
    from datetime import datetime
    from recorder.ui.workers import SmartRecordState
    _ = QApplication.instance() or QApplication([])

    worker = SmartAudioWorker()
    worker.state = SmartRecordState.RECORDING_SPEECH
    worker.session_start_datetime = datetime.now()

    emitted_blocks = []
    worker.rolling_block_ready_signal.connect(lambda idx, st, en, arr, ch: emitted_blocks.append((idx, st, en, ch)))

    # Symulacja 1 sekundy mowy zgromadzonej w buforze tuż przed kliknięciem Mute
    speech_chunk = (np.ones(16000, dtype=np.float32) * 0.1)
    worker.current_mic_block_chunks.append(speech_chunk)

    # Użytkownik klika Mute
    worker.set_mic_muted(True)

    assert len(emitted_blocks) == 1, "Blok mowy powinien zostać natychmiast wyemitowany przy wyciszeniu!"
    assert emitted_blocks[0][3] == "mic"
    assert worker.current_mic_block_chunks == []


def test_hybrid_dual_channel_chronological_sorting():
    """
    Weryfikuje, że w trybie hybrydowym segmenty z Dźwięku Systemu (np. z YouTube)
    odtworzone po wypowiedzi z mikrofonu są sortowane ściśle po mikrofonie (wg wall_start),
    a nie lądują na samej górze pliku z offsetem 0.0s.
    """
    from recorder.core.session import TranscriptionSession

    # Wypowiedź z mikrofonu o 19:02
    mic_turn = {
        "start": 5.0,
        "end": 10.0,
        "speaker": "Mikrofon",
        "text": "Wypowiedź z biura",
        "channel": "mic",
        "wall_start": "2026-09-03T19:02:41.000000",
        "wall_end": "2026-09-03T19:02:46.000000"
    }

    # Wypowiedź z YouTube o 19:04 (nawet jeśli jej lokalny start wynosił 0.0s!)
    sys_turn = {
        "start": 0.0,
        "end": 8.0,
        "speaker": "Dźwięk Systemu",
        "text": "Film z YouTube",
        "channel": "system",
        "wall_start": "2026-09-03T19:04:30.000000",
        "wall_end": "2026-09-03T19:04:38.000000"
    }

    session = TranscriptionSession(turns=[sys_turn, mic_turn])
    plain = session.export_to_plain_text()

    mic_pos = plain.find("Wypowiedź z biura")
    sys_pos = plain.find("Film z YouTube")

    assert mic_pos != -1 and sys_pos != -1
    assert mic_pos < sys_pos, f"BŁĄD: Dźwięk z YouTube znalazł się przed mikrofonem! plain=\n{plain}"


def test_progressbar_audio_timeline_matches_recorded_speech_duration():
    """
    Weryfikuje, że start_sec i end_sec emitowanych bloków odpowiadają rzeczywistej długości
    nagranego audio w pliku WAV (nie rozjeżdżają się z powodu czasu spędzonego w Auto-Pauzie),
    dzięki czemu pasek postępu nigdy nie pokazuje przetworzenia dwukrotności nagrania (np. 5:21 / 2:44).
    """
    from recorder.ui.workers import SmartRecordState
    _ = QApplication.instance() or QApplication([])
    worker = SmartAudioWorker()
    worker.state = SmartRecordState.RECORDING_SPEECH

    emitted_blocks = []
    worker.rolling_block_ready_signal.connect(lambda idx, st, en, arr, ch: emitted_blocks.append((idx, st, en, ch)))

    # 1. Mowa przez 2 sekundy (32000 próbek)
    chunk = (np.ones(16000, dtype=np.float32) * 0.1)
    worker.audio_mixer.add_mic_chunk(chunk)
    worker.audio_mixer.add_mic_chunk(chunk)
    worker.current_mic_block_chunks = [chunk, chunk]
    worker._flush_mic_block()

    assert len(emitted_blocks) == 1
    assert emitted_blocks[0][1] == 0.0
    assert emitted_blocks[0][2] == 2.0  # end_sec = 2.0s

    # 2. Cisza / Auto-Pauza przez 5 minut w świecie rzeczywistym (próbki do WAV nie lecą)
    worker.state = SmartRecordState.AUTO_PAUSED

    # 3. Wznowienie mowy na 3 sekundy (48000 próbek)
    worker.state = SmartRecordState.RECORDING_SPEECH
    worker.audio_mixer.add_mic_chunk(chunk)
    worker.audio_mixer.add_mic_chunk(chunk)
    worker.audio_mixer.add_mic_chunk(chunk)
    worker.current_mic_block_chunks = [chunk, chunk, chunk]
    worker._flush_mic_block()

    assert len(emitted_blocks) == 2
    assert emitted_blocks[1][1] == 2.0  # start_sec = 2.0s (ciągłość w pliku audio!)
    assert emitted_blocks[1][2] == 5.0  # end_sec = 5.0s (dokładnie 5s nagranego audio!)


def test_final_block_without_wall_start_does_not_jump_to_top():
    """
    Weryfikuje, że zaległy blok zfinalizowany po kliknięciu 'Stop i Zapisz'
    (nawet jeśli wall_start jest None lub pusty) zachowuje chronologiczną pozycję
    i nie przeskakuje na sam początek transkrypcji (do indeksu 0).
    """
    from recorder.core.session import TranscriptionSession

    turn_early = {
        "start": 1.5,
        "end": 3.0,
        "speaker": "Mikrofon",
        "text": "Pierwsza wypowiedź",
        "channel": "mic",
        "wall_start": "2026-09-03T23:14:20.000000",
        "wall_end": "2026-09-03T23:14:23.000000"
    }
    turn_middle = {
        "start": 20.0,
        "end": 25.0,
        "speaker": "Dźwięk Systemu",
        "text": "Środkowa wypowiedź",
        "channel": "system",
        "wall_start": "2026-09-03T23:14:40.000000",
        "wall_end": "2026-09-03T23:14:45.000000"
    }
    # Blok finalny po kliknięciu Stop (start na 75s, wall_start=None)
    turn_final = {
        "start": 75.0,
        "end": 80.0,
        "speaker": "Dźwięk Systemu",
        "text": "Ostatnia wypowiedź na sam koniec",
        "channel": "system",
        "wall_start": None,
        "wall_end": None
    }

    session = TranscriptionSession(turns=[turn_final, turn_early, turn_middle])
    plain = session.export_to_plain_text()

    lines = [l.strip() for l in plain.split("\n") if l.strip()]
    assert len(lines) == 3
    assert "Pierwsza wypowiedź" in lines[0], f"Pierwsza powinna być na górze, a jest: {lines[0]}"
    assert "Środkowa wypowiedź" in lines[1]
    assert "Ostatnia wypowiedź" in lines[2], f"Ostatnia powinna być na dole, a jest: {lines[2]}"


def test_smart_audio_worker_device_retry_and_error_handling():
    """
    Weryfikuje, że w przypadku błędu urządzenia audio (np. [Errno -9996] Invalid device na Bluetooth WASAPI),
    SmartAudioWorker ponawia próbę otwarcia strumienia do 3 razy, a w razie trwałego błędu
    emituje error_signal zamiast bezgłośnego zatoru.
    """
    from recorder.ui.workers import SmartAudioWorker, RecordSourceMode
    from unittest.mock import MagicMock, patch

    _ = QApplication.instance() or QApplication([])

    worker = SmartAudioWorker()
    worker.source_mode = RecordSourceMode.MIC_ONLY
    worker._is_running = True

    mock_pyaudio = MagicMock()
    mock_dev_info = {"index": 2, "name": "Redmi Buds 3 Lite", "defaultSampleRate": 16000, "maxInputChannels": 1}
    mock_pyaudio.get_device_info_by_index.return_value = mock_dev_info

    # 1. Scenariusz sukcesu po retry (1. próba rzuca błąd -9996, 2. próba zwraca stream)
    mock_stream = MagicMock()
    mock_stream.is_active.return_value = True
    call_count = 0

    def mock_open_retry(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError(-9996, "Invalid device")
        return mock_stream

    mock_pyaudio.open.side_effect = mock_open_retry

    with patch("recorder.ui.workers.pyaudio.PyAudio", return_value=mock_pyaudio), \
         patch("time.sleep", return_value=None):
        # Symulacja uruchomienia wątku SmartAudioWorker.run() z wczesnym przerwaniem
        with patch.object(worker, "msleep", side_effect=lambda ms: setattr(worker, "_is_running", False)):
            worker.device_index = 2
            worker.run()

    assert call_count == 2, f"Oczekiwano 2 prób (1 błąd, 2 sukces), wykonano {call_count}"
    mock_stream.start_stream.assert_called()

    # 2. Scenariusz trwałego błędu (wszystkie 3 próby zawodzą -> emisja error_signal)
    worker_fail = SmartAudioWorker()
    worker_fail.source_mode = RecordSourceMode.MIC_ONLY
    worker_fail.device_index = 2
    worker_fail._is_running = True

    mock_pyaudio_fail = MagicMock()
    mock_pyaudio_fail.get_device_info_by_index.return_value = mock_dev_info
    mock_pyaudio_fail.open.side_effect = OSError(-9996, "Invalid device")

    received_errors = []
    worker_fail.error_signal.connect(received_errors.append)

    with patch("recorder.ui.workers.pyaudio.PyAudio", return_value=mock_pyaudio_fail), \
         patch("time.sleep", return_value=None):
        worker_fail.run()

    assert mock_pyaudio_fail.open.call_count == 3, "Powinny odbyć się dokładnie 3 próby otwarcia"
    assert len(received_errors) == 1, "Powinien zostać wyemitowany dokładnie jeden sygnał błędu"
    assert "Redmi Buds 3 Lite" in received_errors[0]
    assert "-9996" in received_errors[0]
    assert worker_fail._is_running is False


def test_get_working_input_devices_deduplication_and_wasapi_priority():
    """
    Weryfikuje, że get_working_input_devices():
    1. Deduplikuje ten sam fizyczny mikrofon występujący na wielu host API (MME, DirectSound, WASAPI).
    2. Odrzuca sztuczne aliasy maperów Windows ('Mapowanie dźwięku Microsoft', 'Podstawowy sterownik...').
    3. Przypisuje indeks WASAPI jako primary, a DirectSound i MME jako fallback_indices.
    4. Oznacza domyślny mikrofon systemowy jako is_default=True i umieszcza go na samej górze.
    5. Formatuje czytelną etykietę bez technicznego żargonu w UI.
    """
    from recorder.audio.devices import get_working_input_devices
    from unittest.mock import MagicMock, patch

    mock_p = MagicMock()
    mock_p.get_host_api_count.return_value = 3
    mock_p.get_host_api_info_by_index.side_effect = lambda idx: {
        0: {"index": 0, "name": "MME"},
        1: {"index": 1, "name": "Windows DirectSound"},
        2: {"index": 2, "name": "Windows WASAPI", "defaultInputDevice": 9},
    }[idx]
    mock_p.get_default_input_device_info.return_value = {"index": 1}

    mock_devices = [
        {"index": 0, "name": "Mapowanie dźwięku Microsoft - Input", "hostApi": 0, "maxInputChannels": 2, "defaultSampleRate": 44100, "isLoopbackDevice": False},
        {"index": 1, "name": "Mikrofon (Wireless microphone)", "hostApi": 0, "maxInputChannels": 2, "defaultSampleRate": 44100, "isLoopbackDevice": False},
        {"index": 4, "name": "Podstawowy sterownik przechwytywania dźwięku", "hostApi": 1, "maxInputChannels": 2, "defaultSampleRate": 44100, "isLoopbackDevice": False},
        {"index": 5, "name": "Mikrofon (Wireless microphone)", "hostApi": 1, "maxInputChannels": 2, "defaultSampleRate": 44100, "isLoopbackDevice": False},
        {"index": 9, "name": "Mikrofon (Wireless microphone)", "hostApi": 2, "maxInputChannels": 2, "defaultSampleRate": 48000, "isLoopbackDevice": False},
        {"index": 12, "name": "Mikrofon (Realtek High Definition)", "hostApi": 0, "maxInputChannels": 2, "defaultSampleRate": 44100, "isLoopbackDevice": False},
    ]
    mock_p.get_device_count.return_value = len(mock_devices)
    mock_p.get_device_info_by_index.side_effect = lambda idx: mock_devices[idx] if idx < len(mock_devices) else mock_devices[0]

    with patch("recorder.audio.devices.pyaudio.PyAudio", return_value=mock_p):
        devices = get_working_input_devices()

    # Powinny być dokładnie 2 fizyczne mikrofony (Wireless microphone i Realtek) zamiast 6 wpisów z maperami
    assert len(devices) == 2, f"Oczekiwano 2 zdeduplikowanych mikrofonów, otrzymano {len(devices)}"

    # Pierwszy powinien być domyślny Wireless microphone z indeksem WASAPI (9)
    first = devices[0]
    assert first["name"] == "Mikrofon (Wireless microphone)"
    assert first["index"] == 9, "Główny indeks powinien wskazywać na WASAPI (9)"
    assert first["hostapi"] == "Windows WASAPI"
    assert first["is_default"] is True
    assert "Domyślne" in first["label"]
    assert 5 in first["fallback_indices"], "Fallback powinien zawierać DirectSound (5)"
    assert 1 in first["fallback_indices"], "Fallback powinien zawierać MME (1)"

    # Drugi to Realtek
    second = devices[1]
    assert second["name"] == "Mikrofon (Realtek High Definition)"
    assert second["is_default"] is False


def test_smart_audio_worker_watchdog_recovers_inactive_stream():
    """
    Weryfikuje, że w przypadku utraty aktywności strumienia (is_active() == False na skutek paAbort/błędu),
    Watchdog nie wpada w martwą pętlę start_stream(), lecz zamyka stary strumień i otwiera nowy sprawny obiekt.
    """
    from recorder.ui.workers import SmartAudioWorker, RecordSourceMode, SmartRecordState
    from unittest.mock import MagicMock, patch
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])

    worker = SmartAudioWorker()
    worker.source_mode = RecordSourceMode.MIC_ONLY
    worker._is_running = True
    worker.state = SmartRecordState.RECORDING_SPEECH

    mock_pyaudio = MagicMock()
    mock_pyaudio.get_device_count.return_value = 1
    mock_dev_info = {"index": 9, "name": "Mikrofon (Wireless microphone)", "defaultSampleRate": 48000, "maxInputChannels": 2, "isLoopbackDevice": False}
    mock_pyaudio.get_device_info_by_index.return_value = mock_dev_info

    stream1 = MagicMock()
    stream1.is_active.return_value = True

    stream2 = MagicMock()
    stream2.is_active.return_value = True

    open_calls = []
    def mock_open(**kwargs):
        if not open_calls:
            open_calls.append("stream1")
            return stream1
        open_calls.append("stream2")
        return stream2

    mock_pyaudio.open.side_effect = mock_open

    # W pętli po 1 ticku zmieniamy stream1.is_active() na False, aby Watchdog musiał zrestartować strumień
    simulated_time = [100.0]
    ticks = 0

    def mock_time():
        return simulated_time[0]

    def mock_msleep(ms):
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            # Symulacja zatrzymania strumienia przez sterownik i upływu czasu
            stream1.is_active.return_value = False
            simulated_time[0] += 2.0  # Watchdog sprawdza co 1.5s
        elif ticks >= 3:
            worker._is_running = False

    with patch("recorder.ui.workers.pyaudio.PyAudio", return_value=mock_pyaudio), \
         patch("time.time", side_effect=mock_time), \
         patch("time.sleep", return_value=None):
        with patch.object(worker, "msleep", side_effect=mock_msleep):
            worker.device_index = 9
            worker.run()

    # Stream1 powinien zostać zamknięty, a stream2 otwarty przez Watchdog
    assert len(open_calls) >= 2, f"Oczekiwano restartu strumienia przez Watchdog, otwarto {len(open_calls)} razy"
    stream1.close.assert_called()
    assert stream2.start_stream.called


def test_smart_audio_worker_watchdog_detects_buffer_stall():
    """
    Weryfikuje, że gdy strumień formalnie zwraca is_active() == True, ale dane audio nie przychodzą
    przez ponad 4 sekundy (zamrożenie sterownika USB), Watchdog wykrywa stagnację i resetuje strumień.
    """
    from recorder.ui.workers import SmartAudioWorker, RecordSourceMode, SmartRecordState
    from unittest.mock import MagicMock, patch
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])

    worker = SmartAudioWorker()
    worker.source_mode = RecordSourceMode.MIC_ONLY
    worker._is_running = True
    worker.state = SmartRecordState.RECORDING_SPEECH

    mock_pyaudio = MagicMock()
    mock_pyaudio.get_device_count.return_value = 1
    mock_dev_info = {"index": 9, "name": "Mikrofon (Wireless microphone)", "defaultSampleRate": 48000, "maxInputChannels": 2, "isLoopbackDevice": False}
    mock_pyaudio.get_device_info_by_index.return_value = mock_dev_info

    stream1 = MagicMock()
    stream1.is_active.return_value = True

    stream2 = MagicMock()
    stream2.is_active.return_value = True

    open_calls = []
    def mock_open(**kwargs):
        if not open_calls:
            open_calls.append("stream1")
            return stream1
        open_calls.append("stream2")
        return stream2

    mock_pyaudio.open.side_effect = mock_open

    # Symulacja upływu czasu: najpierw t=0, a po pierwszym ticku skok czasu o 5 sekund bez wywołania callbacku
    simulated_time = [100.0]
    ticks = 0

    def mock_time():
        return simulated_time[0]

    def mock_msleep(ms):
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            simulated_time[0] += 5.0  # Skok o 5 sekund (stagnacja bufora > 4s)
        elif ticks >= 3:
            worker._is_running = False

    with patch("recorder.ui.workers.pyaudio.PyAudio", return_value=mock_pyaudio), \
         patch("time.time", side_effect=mock_time), \
         patch("time.sleep", return_value=None):
        with patch.object(worker, "msleep", side_effect=mock_msleep):
            worker.device_index = 9
            worker.run()

    # Stagnacja bufora powinna doprowadzić do wywołania _reopen_mic_stream() i otwarcia stream2
    assert len(open_calls) >= 2, f"Oczekiwano restartu po stagnacji bufora, otwarto {len(open_calls)} razy"
    stream1.close.assert_called()


def test_smart_audio_worker_initial_fallback_candidate():
    """
    Weryfikuje, że gdy podczas startu otwarcie wybranego indeksu urządzenia (np. WASAPI) rzuci błąd,
    SmartAudioWorker automatycznie próbuje wariantów fallback (DirectSound/MME) tego samego mikrofonu.
    """
    from recorder.ui.workers import SmartAudioWorker, RecordSourceMode
    from unittest.mock import MagicMock, patch
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])

    worker = SmartAudioWorker()
    worker.source_mode = RecordSourceMode.MIC_ONLY
    worker._is_running = True

    mock_pyaudio = MagicMock()
    mock_pyaudio.get_device_count.return_value = 2
    dev_wasapi = {"index": 9, "name": "Mikrofon (Wireless microphone)", "defaultSampleRate": 48000, "maxInputChannels": 2, "isLoopbackDevice": False}
    dev_mme = {"index": 1, "name": "Mikrofon (Wireless microphone)", "defaultSampleRate": 44100, "maxInputChannels": 2, "isLoopbackDevice": False}

    mock_pyaudio.get_device_info_by_index.side_effect = lambda idx: dev_wasapi if idx == 9 else dev_mme

    fallback_stream = MagicMock()
    fallback_stream.is_active.return_value = True

    def mock_open(**kwargs):
        if kwargs.get("input_device_index") == 9:
            raise OSError(-9997, "Invalid sample rate on WASAPI")
        return fallback_stream

    mock_pyaudio.open.side_effect = mock_open

    with patch("recorder.ui.workers.pyaudio.PyAudio", return_value=mock_pyaudio), \
         patch("time.sleep", return_value=None):
        with patch.object(worker, "msleep", side_effect=lambda ms: setattr(worker, "_is_running", False)):
            worker.device_index = 9
            worker.run()

    fallback_stream.start_stream.assert_called()






