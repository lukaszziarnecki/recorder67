import os
import sys
import copy
import uuid
import pytest
from unittest.mock import MagicMock

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
