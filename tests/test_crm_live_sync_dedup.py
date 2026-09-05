import os
import sys
import copy
import uuid
import pytest
from unittest.mock import MagicMock, patch

# Ustawienie ścieżki do projektu
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from recorder.core.session import get_turn_sync_id
from recorder.core.diarizer import format_transcript_without_diarization


def test_get_turn_sync_id_stability():
    """
    Weryfikuje, że get_turn_sync_id zwraca ten sam stabilny identyfikator
    niezależnie od kopii słownika w pamięci (zmiana id(t) w Pythonie).
    """
    turn_with_uuid = {
        "id": "bfff12fb-cf09-4760-be1d-7b75f6e2e663",
        "start": 10.5,
        "end": 14.2,
        "channel": "mic",
        "speaker": "Mikrofon",
        "text": "Dzień dobry, zaczynamy spotkanie."
    }
    
    # Utwórz głęboką kopię (inny adres w pamięci RAM)
    copied_turn = copy.deepcopy(turn_with_uuid)
    assert id(turn_with_uuid) != id(copied_turn)
    assert get_turn_sync_id(turn_with_uuid) == get_turn_sync_id(copied_turn)
    assert get_turn_sync_id(turn_with_uuid) == "bfff12fb-cf09-4760-be1d-7b75f6e2e663"

    # Test fallbacku dla tury bez id
    turn_without_id = {
        "start": 1.25,
        "end": 4.50,
        "channel": "system",
        "speaker": "Dźwięk Systemu",
        "text": "Dźwięk z prezentacji"
    }
    copied_no_id = copy.deepcopy(turn_without_id)
    assert id(turn_without_id) != id(copied_no_id)
    assert get_turn_sync_id(turn_without_id) == get_turn_sync_id(copied_no_id)
    assert get_turn_sync_id(turn_without_id) == "system_1.25_4.5_Dźwięk z prezentacji"


def test_format_transcript_without_diarization_assigns_unique_uuid():
    """
    Weryfikuje, że format_transcript_without_diarization automatycznie nadaje
    unikalny identyfikator UUID dla każdej wygenerowanej tury wypowiedzi.
    """
    words = [
        {"word": "Cześć", "start": 0.0, "end": 0.5},
        {"word": "wszystkim.", "start": 0.6, "end": 1.2},
        {"word": "Zaczynamy.", "start": 3.0, "end": 4.0}
    ]
    _, _, turns = format_transcript_without_diarization(words)
    assert len(turns) >= 2
    for t in turns:
        assert "id" in t
        # Weryfikacja formatu UUID
        parsed_uuid = uuid.UUID(t["id"])
        assert str(parsed_uuid) == t["id"]
    
    # Upewnij się, że każda tura ma inny UUID
    ids = [t["id"] for t in turns]
    assert len(ids) == len(set(ids))


def test_crm_live_sync_prevents_on2_duplication():
    """
    Symulacja mechanizmu okna głównego (_on_rolling_block_processed):
    Weryfikuje, że kolejne bloki audio w sesji (w tym bloki puste/pominięte
    oraz deserializowane słowniki o nowych id() w Pythonie) NIE powodują ponownego
    wysyłania starych wypowiedzi do CRM.
    """
    # Symulacja stanu okna głównego
    _synced_turn_ids = set()
    mock_cloud_sync = MagicMock()
    mock_cloud_sync.config = {"live_streaming": True, "auto_sync": True}
    current_meeting_id = "test-meeting-uuid"

    # Blok 1: 2 wypowiedzi
    turn1 = {"id": str(uuid.uuid4()), "start": 0.0, "end": 3.0, "speaker": "Mikrofon", "text": "Pierwsza wypowiedź"}
    turn2 = {"id": str(uuid.uuid4()), "start": 3.5, "end": 7.0, "speaker": "Mikrofon", "text": "Druga wypowiedź"}
    all_turns_block1 = [turn1, turn2]

    # Symulacja odbioru sygnału PySide6 (kopie słowników o nowych id(t))
    received_turns_1 = copy.deepcopy(all_turns_block1)
    
    # Logika z _on_rolling_block_processed z użyciem get_turn_sync_id
    new_segments_1 = [t for t in received_turns_1 if get_turn_sync_id(t) not in _synced_turn_ids]
    for t in new_segments_1:
        _synced_turn_ids.add(get_turn_sync_id(t))
    if new_segments_1:
        mock_cloud_sync.append_live_segments_async(
            meeting_id=current_meeting_id,
            new_segments=new_segments_1
        )

    assert len(new_segments_1) == 2
    assert mock_cloud_sync.append_live_segments_async.call_count == 1
    assert mock_cloud_sync.append_live_segments_async.call_args[1]["new_segments"] == new_segments_1

    # Blok 2: Dochodzi trzecia wypowiedź (all_turns ma 3 elementy)
    turn3 = {"id": str(uuid.uuid4()), "start": 8.0, "end": 12.0, "speaker": "Mikrofon", "text": "Trzecia wypowiedź"}
    all_turns_block2 = [turn1, turn2, turn3]
    received_turns_2 = copy.deepcopy(all_turns_block2)

    new_segments_2 = [t for t in received_turns_2 if get_turn_sync_id(t) not in _synced_turn_ids]
    for t in new_segments_2:
        _synced_turn_ids.add(get_turn_sync_id(t))
    if new_segments_2:
        mock_cloud_sync.append_live_segments_async(
            meeting_id=current_meeting_id,
            new_segments=new_segments_2
        )

    # Tylko 1 nowy segment w bloku 2!
    assert len(new_segments_2) == 1
    assert new_segments_2[0]["text"] == "Trzecia wypowiedź"
    assert mock_cloud_sync.append_live_segments_async.call_count == 2

    # Blok 3: Blok pusty / cisza (brak nowych słów w Whisperze, emitowane dotychczasowe all_turns)
    received_turns_3 = copy.deepcopy(all_turns_block2)
    new_segments_3 = [t for t in received_turns_3 if get_turn_sync_id(t) not in _synced_turn_ids]
    for t in new_segments_3:
        _synced_turn_ids.add(get_turn_sync_id(t))
    if new_segments_3:
        mock_cloud_sync.append_live_segments_async(
            meeting_id=current_meeting_id,
            new_segments=new_segments_3
        )

    # 0 nowych segmentów, append_live_segments_async NIE powiększa liczby wywołań
    assert len(new_segments_3) == 0
    assert mock_cloud_sync.append_live_segments_async.call_count == 2

    # Blok 4: Dźwięk systemowy w trybie hybrydowym (posortowany chronologicznie w środku osi czasu)
    turn4_sys = {"id": str(uuid.uuid4()), "start": 4.0, "end": 6.0, "channel": "system", "speaker": "Dźwięk Systemu", "text": "Głos z Teams"}
    # Włożony w środek listy
    all_turns_block4 = [turn1, turn4_sys, turn2, turn3]
    received_turns_4 = copy.deepcopy(all_turns_block4)

    new_segments_4 = [t for t in received_turns_4 if get_turn_sync_id(t) not in _synced_turn_ids]
    for t in new_segments_4:
        _synced_turn_ids.add(get_turn_sync_id(t))
    if new_segments_4:
        mock_cloud_sync.append_live_segments_async(
            meeting_id=current_meeting_id,
            new_segments=new_segments_4
        )

    # Tylko 1 nowy segment, mimo że wpadł w środek listy chronologicznej!
    assert len(new_segments_4) == 1
    assert new_segments_4[0]["text"] == "Głos z Teams"
    assert mock_cloud_sync.append_live_segments_async.call_count == 3

    # Łącznie wysłano dokładnie 4 unikalne segmenty w 4 blokach:
    assert len(_synced_turn_ids) == 4


def test_append_live_segments_uses_merge_duplicates_header():
    """
    Weryfikuje, że zapytanie HTTP do PostgREST zawiera nagłówek
    'Prefer: return=minimal,resolution=merge-duplicates' zabezpieczający przed błędem 409 Conflict.
    """
    from recorder.core.cloud_sync import CloudSyncManager
    manager = CloudSyncManager()
    old_config = dict(manager.config)
    try:
        manager.config.update({
            "sync_target": "emanager",
            "supabase_url": "https://test.supabase.co",
            "supabase_key": "test-anon-key"
        })

        segments = [{"id": str(uuid.uuid4()), "start": 0.0, "end": 2.0, "speaker": "Mikrofon", "text": "Test"}]

        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            manager._append_live_segments_worker("test-meet-id", segments, "Test", 2.0, 1)

            assert mock_urlopen.call_count >= 1
            req = mock_urlopen.call_args_list[0][0][0]
            assert "resolution=merge-duplicates" in req.headers.get("Prefer", "")
    finally:
        manager.config = old_config


def test_append_live_segments_retries_on_network_failure_without_loss():
    """
    Weryfikuje odporność na awarię sieci: jeśli zapytanie HTTP w bloku #1 rzuci błąd
    (np. timeout / brak sieci), segmenty nie przepadają, lecz zostają w buforze pending
    i są pomyślnie wysyłane wraz z kolejnym blokiem #2.
    """
    from recorder.core.cloud_sync import CloudSyncManager
    import urllib.error
    import json

    manager = CloudSyncManager()
    old_config = dict(manager.config)
    try:
        manager.config.update({
            "sync_target": "emanager",
            "supabase_url": "https://test.supabase.co",
            "supabase_key": "test-anon-key"
        })
        with manager._live_sync_lock:
            manager._pending_live_segments = []

        seg1 = {"id": str(uuid.uuid4()), "start": 0.0, "end": 2.0, "speaker": "Mikrofon", "text": "Pierwsza część"}
        seg2 = {"id": str(uuid.uuid4()), "start": 2.5, "end": 5.0, "speaker": "Mikrofon", "text": "Druga część"}

        # Blok 1: awaria sieci (URLError / timeout)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection timed out")):
            manager._append_live_segments_worker("test-meet-id", [seg1], "Pierwsza część", 2.0, 1)

        # Segment seg1 NIE przepadł – pozostał w buforze pending!
        with manager._live_sync_lock:
            assert len(manager._pending_live_segments) == 1
            assert manager._pending_live_segments[0]["id"] == seg1["id"]

        # Blok 2: sieć wraca – dochodzi seg2
        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            manager._append_live_segments_worker("test-meet-id", [seg2], "Pierwsza część Druga część", 5.0, 1)

            # Sprawdź dane wysłane w POST do meeting_segments
            req = mock_urlopen.call_args_list[0][0][0]
            posted_rows = json.loads(req.data.decode("utf-8"))
            # Obie wypowiedzi (zaległa seg1 oraz nowa seg2) zostały wysłane w jednej paczce!
            assert len(posted_rows) == 2
            ids = [r["id"] for r in posted_rows]
            assert seg1["id"] in ids
            assert seg2["id"] in ids

        # Po udanej wysyłce bufor pending jest pusty
        with manager._live_sync_lock:
            assert len(manager._pending_live_segments) == 0
    finally:
        manager.config = old_config


def test_save_segments_to_supabase_handles_sparse_keys():
    """
    Weryfikuje, że metoda _save_segments_to_supabase bezpiecznie obsługuje słowniki
    o brakujących kluczach bez rzucania wyjątku KeyError.
    """
    from recorder.core.cloud_sync import CloudSyncManager
    import json

    manager = CloudSyncManager()
    sparse_segments = [
        {"speaker": "Jan", "start": 1.0, "end": 2.0, "text": "Dzień dobry"},
        {"text": "Brak mówcy i czasów"},  # brak speaker, start, end
        {}  # całkowicie pusty słownik
    ]

    mock_resp = MagicMock()
    mock_resp.status = 201
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        manager._save_segments_to_supabase(
            base_url="https://test.supabase.co",
            headers={"apikey": "test"},
            meeting_id="test-meet-id",
            segments=sparse_segments
        )
        assert mock_urlopen.call_count >= 2  # DELETE + POST
        post_req = mock_urlopen.call_args_list[1][0][0]
        rows = json.loads(post_req.data.decode("utf-8"))
        assert len(rows) == 3
        assert rows[1]["speaker_name"] == "Mówca"
        assert rows[1]["start_time"] == 0.0
        assert rows[2]["text"] == ""
        assert "resolution=merge-duplicates" in post_req.headers.get("Prefer", "")

