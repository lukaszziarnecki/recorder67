import os
import sys
import json
import re
import tempfile
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta


def extract_datetime_from_filename(filepath: str) -> Optional[datetime]:
    """Pobiera dokładną datę i godzinę rozpoczęcia nagrania z nazwy pliku."""
    if not filepath:
        return None
    base = os.path.basename(filepath)
    import re
    m = re.search(r'(\d{8})_(\d{6})', base)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S")
        except Exception:
            pass
    return None


def format_turn_timestamp(st: float, en: float, session_start_time: Optional[datetime] = None, ts_format: Optional[str] = None,
                          wall_start: Optional[Any] = None, wall_end: Optional[Any] = None) -> str:
    """Formatuje znacznik czasu dla wypowiedzi zgodnie z ustawieniami użytkownika (offset, godzina, hybryda)."""
    if ts_format is None:
        try:
            from recorder.config import get_timestamp_format
            ts_format = get_timestamp_format()
        except Exception:
            ts_format = "offset_only"

    s_min, s_sec = int(st // 60), int(st % 60)
    e_min, e_sec = int(en // 60), int(en % 60)
    offset_label = f"{s_min:02d}:{s_sec:02d} - {e_min:02d}:{e_sec:02d}"

    if ts_format == "offset_only":
        return offset_label

    w_start_dt = None
    w_end_dt = None
    if wall_start is not None and wall_end is not None:
        try:
            w_start_dt = datetime.fromisoformat(wall_start) if isinstance(wall_start, str) else wall_start
            w_end_dt = datetime.fromisoformat(wall_end) if isinstance(wall_end, str) else wall_end
        except Exception:
            w_start_dt = None
            w_end_dt = None

    if w_start_dt is not None and w_end_dt is not None:
        clock_start = w_start_dt.strftime("%H:%M:%S")
        clock_end = w_end_dt.strftime("%H:%M:%S")
        clock_label = f"{clock_start} - {clock_end}"
    elif session_start_time is not None:
        from datetime import timedelta
        real_start = session_start_time + timedelta(seconds=st)
        real_end = session_start_time + timedelta(seconds=en)
        clock_start = real_start.strftime("%H:%M:%S")
        clock_end = real_end.strftime("%H:%M:%S")
        clock_label = f"{clock_start} - {clock_end}"
    else:
        clock_label = None

    if ts_format == "clock_only" and clock_label:
        return clock_label
    elif ts_format == "offset_only":
        return offset_label
    elif clock_label:
        return f"{offset_label} | {clock_label}"
    else:
        return offset_label


def turn_sort_key(turn: Dict[str, Any], session_start_time: Optional[datetime] = None) -> Tuple[str, float]:
    """
    Zwraca stabilny klucz sortowania (wall_timestamp_str, start_offset)
    gwarantujący chronologiczną kolejność wypowiedzi na osi czasu.
    W przypadku braku 'wall_start' nie zwraca pustego stringa (co wrzucało turn na początek listy),
    lecz wyznacza czas na podstawie offsetu startu.
    """
    ws = turn.get("wall_start")
    st = float(turn.get("start", 0.0))
    if not ws:
        from datetime import timedelta
        if session_start_time is not None:
            ws = (session_start_time + timedelta(seconds=st)).isoformat()
        else:
            ws = f"OFFSET_{st:012.3f}"
    return (ws, st)


def get_turn_sync_id(turn: Dict[str, Any]) -> str:
    """
    Zwraca stabilny identyfikator wypowiedzi (turn), odporny na serializację
    i rekonstrukcję słowników przez mechanizm sygnałów PySide6.
    """
    tid = turn.get("id") or turn.get("turn_id")
    if tid:
        return str(tid)
    ch = str(turn.get("channel") or "mic")
    st_val = turn.get("start")
    en_val = turn.get("end")
    st = round(float(st_val if st_val is not None else 0.0), 2)
    en = round(float(en_val if en_val is not None else 0.0), 2)
    txt = str(turn.get("text") or "").strip()
    return f"{ch}_{st}_{en}_{txt}"


class TranscriptionSession:
    """
    Struktura danych reprezentująca kompletną sesję transkrypcji i diaryzacji.
    Zapisywana jako plik .json na dysku obok pliku .txt i nagrania .wav.
    Umożliwia niezależne, modułowe uruchamianie diaryzacji PyAnnote bez konieczności
    ponownego przetwarzania audio przez Whisper.
    """
    def __init__(
        self,
        source_audio: str = "",
        prepared_wav: str = "",
        duration_sec: float = 0.0,
        created_at: Optional[str] = None,
        whisper_model: str = "",
        has_transcription: bool = False,
        has_diarization: bool = False,
        speakers_detected: Optional[List[str]] = None,
        speaker_mapping: Optional[Dict[str, str]] = None,
        words: Optional[List[Dict[str, Any]]] = None,
        turns: Optional[List[Dict[str, Any]]] = None,
        meeting_id: Optional[str] = None,
        version: int = 1
    ):
        self.version = version
        self.meeting_id = meeting_id
        self.source_audio = source_audio
        self.prepared_wav = prepared_wav
        self.duration_sec = duration_sec
        self.created_at = created_at or datetime.now().isoformat()
        self.whisper_model = whisper_model
        self.has_transcription = has_transcription
        self.has_diarization = has_diarization
        self.speakers_detected = speakers_detected or []
        self.speaker_mapping = speaker_mapping or {}
        self.words = sorted(words or [], key=lambda w: float(w.get("start", 0.0)))
        self.turns = sorted(turns or [], key=lambda t: float(t.get("start", 0.0)))

    @property
    def speakers_count(self) -> int:
        if self.speakers_detected:
            return len(self.speakers_detected)
        spks = set()
        for t in self.turns:
            spk = t.get("speaker")
            if spk and spk != "Mówca":
                spks.add(spk)
        return len(spks)

    def to_dict(self) -> Dict[str, Any]:
        from datetime import datetime, date
        clean_turns = []
        for t in self.turns:
            ct = dict(t)
            for k in ("wall_start", "wall_end"):
                v = ct.get(k)
                if isinstance(v, (datetime, date)):
                    ct[k] = v.isoformat()
            clean_turns.append(ct)

        return {
            "version": self.version,
            "meeting_id": self.meeting_id,
            "source_audio": self.source_audio,
            "prepared_wav": self.prepared_wav,
            "created_at": self.created_at,
            "duration_sec": round(self.duration_sec, 2),
            "status": {
                "has_transcription": self.has_transcription,
                "has_diarization": self.has_diarization,
                "whisper_model": self.whisper_model,
                "speakers_count": self.speakers_count,
                "speakers_detected": self.speakers_detected
            },
            "speaker_mapping": self.speaker_mapping,
            "words": self.words,
            "turns": clean_turns
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptionSession":
        status = data.get("status", {})
        return cls(
            meeting_id=data.get("meeting_id"),
            source_audio=data.get("source_audio", ""),
            prepared_wav=data.get("prepared_wav", ""),
            duration_sec=data.get("duration_sec", 0.0),
            created_at=data.get("created_at"),
            whisper_model=status.get("whisper_model", ""),
            has_transcription=status.get("has_transcription", False),
            has_diarization=status.get("has_diarization", False),
            speakers_detected=status.get("speakers_detected", []),
            speaker_mapping=data.get("speaker_mapping", {}),
            words=data.get("words", []),
            turns=data.get("turns", []),
            version=data.get("version", 1)
        )

    def save_to_json(self, json_path: str) -> bool:
        """
        Atomowy i bezpieczny zapis danych sesji do pliku JSON w kodowaniu UTF-8.
        """
        try:
            parent_dir = os.path.dirname(os.path.abspath(json_path))
            os.makedirs(parent_dir, exist_ok=True)

            data = self.to_dict()
            json_text = json.dumps(data, ensure_ascii=False, indent=2, default=str)

            temp_fd, temp_path = tempfile.mkstemp(dir=parent_dir, prefix="session_", suffix=".tmp")
            try:
                with open(temp_fd, "w", encoding="utf-8") as f:
                    f.write(json_text)

                if os.path.exists(json_path):
                    os.replace(temp_path, json_path)
                else:
                    os.rename(temp_path, json_path)

                return True
            except Exception:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                raise
        except Exception as e:
            print(f"⚠️ [SESJA] Błąd zapisu pliku sesji JSON '{json_path}': {e}")
            return False

    @classmethod
    def load_from_json(cls, json_path: str) -> Optional["TranscriptionSession"]:
        """
        Wczytuje sesję z pliku JSON. Zwraca obiekt TranscriptionSession lub None przy błędzie.
        """
        if not os.path.exists(json_path):
            return None

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception as e:
            print(f"⚠️ [SESJA] Błąd odczytu pliku sesji JSON '{json_path}': {e}")
            return None

    def export_to_plain_text(self, speaker_mapping: Optional[Dict[str, str]] = None,
                             session_start_time: Optional[datetime] = None) -> str:
        """
        Generuje czytelny tekst sformatowany z timestampami i nazwami mówców.
        """
        mapping = dict(self.speaker_mapping)
        if speaker_mapping:
            mapping.update(speaker_mapping)

        base_dt = session_start_time
        if base_dt is None and self.created_at:
            try:
                base_dt = datetime.fromisoformat(self.created_at)
            except Exception:
                base_dt = None
        if base_dt is None:
            base_dt = extract_datetime_from_filename(self.prepared_wav) or extract_datetime_from_filename(self.source_audio)

        sorted_turns = sorted(self.turns, key=lambda t: turn_sort_key(t, base_dt))
        lines = []
        for t in sorted_turns:
            spk = t.get("speaker", "Mówca")
            display_spk = mapping.get(spk, spk)
            st = float(t.get("start", 0.0))
            en = float(t.get("end", 0.0))
            txt = t.get("text", "").strip()

            time_label = format_turn_timestamp(st, en, base_dt, wall_start=t.get("wall_start"), wall_end=t.get("wall_end"))
            lines.append(f"[{time_label}] {display_spk}: {txt}\n")

        return "\n".join(lines).strip()

    def export_to_html(self, speaker_mapping: Optional[Dict[str, str]] = None,
                       session_start_time: Optional[datetime] = None,
                       reverse_order: Optional[bool] = None) -> str:
        """
        Generuje sformatowany kod HTML do wyświetlenia w QTextEdit.
        """
        mapping = dict(self.speaker_mapping)
        if speaker_mapping:
            mapping.update(speaker_mapping)

        base_dt = session_start_time
        if base_dt is None and self.created_at:
            try:
                base_dt = datetime.fromisoformat(self.created_at)
            except Exception:
                base_dt = None
        if base_dt is None:
            base_dt = extract_datetime_from_filename(self.prepared_wav) or extract_datetime_from_filename(self.source_audio)

        if reverse_order is None:
            try:
                from recorder.config import get_preview_order
                reverse_order = (get_preview_order() == "newest_first")
            except Exception:
                reverse_order = True

        sorted_turns = sorted(self.turns, key=lambda t: turn_sort_key(t, base_dt))
        display_turns = list(reversed(sorted_turns)) if reverse_order else sorted_turns

        html_blocks = []
        for t in display_turns:
            spk = t.get("speaker", "Mówca")
            display_spk = mapping.get(spk, spk)
            st = float(t.get("start", 0.0))
            en = float(t.get("end", 0.0))
            txt = t.get("text", "").strip()

            time_label = format_turn_timestamp(st, en, base_dt, wall_start=t.get("wall_start"), wall_end=t.get("wall_end"))
            html_blocks.append(f"<b>[{time_label}] {display_spk}:</b> {txt}<br><br>")

        return "".join(html_blocks).strip()

    def update_speaker_mapping(self, new_mapping: Dict[str, str]):
        """
        Aktualizuje słownik mapowania mówców i synchronizuje go w sesji.
        """
        if not self.speaker_mapping:
            self.speaker_mapping = {}
        for spk_id, name in new_mapping.items():
            if name and name.strip():
                self.speaker_mapping[spk_id] = name.strip()
            else:
                self.speaker_mapping[spk_id] = spk_id

    def get_status_badge(self) -> str:
        """
        Zwraca etykietę statusu sesji, np. '[👥 Mówcy (3 os.)]' lub '[📝 Tylko tekst]'.
        """
        if self.has_diarization:
            cnt = self.speakers_count
            return f"[👥 Mówcy ({cnt} os.)]" if cnt > 0 else "[👥 Mówcy]"
        elif self.has_transcription:
            return "[📝 Tylko tekst]"
        return "[⏳ W toku]"


def get_session_path_for_txt(txt_path: str) -> str:
    """Zwraca ścieżkę do pliku .json odpowiadającego danemu plikowi .txt."""
    base, _ = os.path.splitext(txt_path)
    return f"{base}.json"


def get_session_path_for_audio(audio_path: str, transcriptions_dir: str) -> str:
    """Zwraca ścieżkę do pliku .json dla podanego pliku audio."""
    base_name = os.path.basename(audio_path)
    file_stem = os.path.splitext(base_name)[0]
    clean_stem = file_stem.replace("inteligentne_nagranie_", "")
    return os.path.join(transcriptions_dir, f"transkrypcja_{clean_stem}.json")


def get_txt_path_for_audio(audio_path: str, transcriptions_dir: str) -> str:
    """Zwraca ścieżkę do pliku .txt dla podanego pliku audio."""
    base_name = os.path.basename(audio_path)
    file_stem = os.path.splitext(base_name)[0]
    clean_stem = file_stem.replace("inteligentne_nagranie_", "")
    return os.path.join(transcriptions_dir, f"transkrypcja_{clean_stem}.txt")


def find_existing_session_for_audio(audio_path: str, transcriptions_dir: str) -> Optional[Tuple[str, TranscriptionSession]]:
    """
    Sprawdza, czy dla podanego pliku audio istnieje już gotowy plik sesji .json z transkrypcją.
    Zwraca (json_path, session) lub None.
    """
    if not os.path.exists(transcriptions_dir):
        return None

    base_name = os.path.basename(audio_path)
    file_stem = os.path.splitext(base_name)[0]
    clean_stem = file_stem.replace("inteligentne_nagranie_", "").replace("_16k", "")

    # 1. Sprawdzenie bezpośredniej ścieżki
    direct_path = get_session_path_for_audio(audio_path, transcriptions_dir)
    if os.path.exists(direct_path):
        session = TranscriptionSession.load_from_json(direct_path)
        if session and session.has_transcription and session.words:
            return direct_path, session

    # 2. Przeszukiwanie katalogu
    for fname in os.listdir(transcriptions_dir):
        if fname.endswith(".json"):
            json_stem = fname.replace("transkrypcja_", "").replace(".json", "")
            if clean_stem in json_stem or json_stem in clean_stem or base_name in fname:
                full_json_path = os.path.join(transcriptions_dir, fname)
                session = TranscriptionSession.load_from_json(full_json_path)
                if session and session.has_transcription and session.words:
                    return full_json_path, session

    return None
