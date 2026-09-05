"""
Moduł synchronizacji chmurowej dla recorder67 (Agnostic Cloud Sync).
Odpowiada za asynchroniczne wysyłanie transkrypcji i metadanych spotkań
do bazy Supabase (REST API) lub zewnętrznego Webhooka (klienci B2B / n8n / CRM).
Obsługuje automatyczną kolejkę offline (odporność na brak internetu).
"""


import os
import json
import time
import uuid
import logging
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import urllib.request
import urllib.parse
import urllib.error

from PySide6.QtCore import QObject, Signal as pyqtSignal

from recorder.config import get_cloud_sync_config, SYNC_QUEUE_DIR

logger = logging.getLogger(__name__)


class CloudSyncSignals(QObject):
    sync_started = pyqtSignal(str)              # meeting_id
    sync_finished = pyqtSignal(str, bool, str)  # meeting_id, success, message
    offline_queued = pyqtSignal(str, str)       # meeting_id, reason
    live_session_started = pyqtSignal(str)      # meeting_id
    live_block_synced = pyqtSignal(str, int)    # meeting_id, total_segments_synced
    live_session_finalized = pyqtSignal(str, bool, str)  # meeting_id, success, message


class CloudSyncManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(CloudSyncManager, cls).__new__(cls)
                cls._instance._init_manager()
            return cls._instance

    def _init_manager(self):
        self.signals = CloudSyncSignals()
        self.config = get_cloud_sync_config()
        self._is_processing_queue = False
        self._live_sync_lock = threading.Lock()
        self._pending_live_segments: List[Dict[str, Any]] = []
        self._latest_synced_duration: float = 0.0
        os.makedirs(SYNC_QUEUE_DIR, exist_ok=True)

    def reload_config(self):
        """Przeładowuje konfigurację z pliku .env/środowiska."""
        self.config = get_cloud_sync_config()

    def start_live_session_async(
        self,
        title: Optional[str] = None,
        context_type: str = "general",
        context_id: Optional[str] = None,
        meeting_id: Optional[str] = None,
    ) -> str:
        """
        Inicjalizuje nowe spotkanie biurowe w Supabase ze statusem 'recording' (dla transmisji na żywo do CRM).
        Zwraca wygenerowany meeting_id.
        """
        self.reload_config()
        if not meeting_id:
            meeting_id = str(uuid.uuid4())

        with self._live_sync_lock:
            self._pending_live_segments = []
            self._latest_synced_duration = 0.0

        title_str = title or f"Spotkanie biurowe {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        threading.Thread(
            target=self._start_live_session_worker,
            args=(meeting_id, title_str, context_type, context_id),
            daemon=True
        ).start()

        return meeting_id

    def append_live_segments_async(
        self,
        meeting_id: str,
        new_segments: List[Dict[str, Any]],
        full_transcript: str,
        duration_seconds: float = 0.0,
        speaker_count: int = 1,
    ):
        """
        Asynchronicznie przesyła nowo przetworzone segmenty mowy do Supabase
        i aktualizuje nagłówek spotkania (transkrypcję, czas trwania).
        """
        self.reload_config()
        threading.Thread(
            target=self._append_live_segments_worker,
            args=(meeting_id, new_segments, full_transcript, duration_seconds, speaker_count),
            daemon=True
        ).start()

    def finalize_live_session_async(
        self,
        meeting_id: str,
        final_transcript: str,
        duration_seconds: float = 0.0,
        audio_path: Optional[str] = None,
        turns: Optional[List[Dict[str, Any]]] = None,
        title: Optional[str] = None,
    ):
        """
        Finalizuje sesję spotkania w Supabase (zmienia status na 'completed', kompresuje i wgrywa audio).
        """
        self.reload_config()
        with self._live_sync_lock:
            self._pending_live_segments = []
            self._latest_synced_duration = 0.0
        threading.Thread(
            target=self._finalize_live_session_worker,
            args=(meeting_id, final_transcript, duration_seconds, audio_path, turns, title),
            daemon=True
        ).start()

    def sync_meeting_async(
        self,
        title: str,
        transcript_text: str,
        segments: List[Dict[str, Any]],
        duration_seconds: float = 0.0,
        audio_path: Optional[str] = None,
        context_type: str = "general",
        context_id: Optional[str] = None,
        meeting_id: Optional[str] = None,
    ) -> str:
        """
        Asynchronicznie wysyła kompletne spotkanie do chmury (nie blokuje wątku UI).
        Zwraca wygenerowany meeting_id.
        """
        self.reload_config()
        if not meeting_id:
            if audio_path:
                audio_stem = os.path.splitext(os.path.basename(audio_path))[0].replace("_16k", "").replace("inteligentne_nagranie_", "")
                meeting_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"recorder67_{audio_stem}"))
            else:
                meeting_id = str(uuid.uuid4())

        payload = self._build_payload(
            meeting_id=meeting_id,
            title=title,
            transcript_text=transcript_text,
            segments=segments,
            duration_seconds=duration_seconds,
            audio_path=audio_path,
            context_type=context_type,
            context_id=context_id,
        )

        threading.Thread(
            target=self._sync_worker,
            args=(payload,),
            daemon=True
        ).start()

        return meeting_id

    def _build_payload(
        self,
        meeting_id: str,
        title: str,
        transcript_text: str,
        segments: List[Dict[str, Any]],
        duration_seconds: float,
        audio_path: Optional[str],
        context_type: str,
        context_id: Optional[str],
    ) -> Dict[str, Any]:
        """Tworzy znormalizowany, agnostyczny obiekt spotkania."""
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Zlicz unikalnych mówców
        unique_speakers = set()
        cleaned_segments = []
        for s in segments:
            spk = s.get("speaker", "Mówca")
            unique_speakers.add(spk)
            cleaned_segments.append({
                "speaker": spk,
                "start": float(s.get("start", 0.0)),
                "end": float(s.get("end", 0.0)),
                "text": str(s.get("text", "")).strip()
            })

        return {
            "id": meeting_id,
            "title": title or f"Spotkanie {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "organization_id": self.config.get("organization_id", "default_org"),
            "device_name": self.config.get("device_name", "Biuro-Stanowisko"),
            "created_at": now_iso,
            "duration_seconds": round(duration_seconds, 2),
            "speaker_count": len(unique_speakers),
            "transcript_text": transcript_text.strip(),
            "segments": cleaned_segments,
            "context_type": context_type,
            "context_id": context_id,
            "audio_path": audio_path if audio_path and os.path.exists(audio_path) else None,
            "source": "recorder67_ambient",
        }

    def _sync_worker(self, payload: Dict[str, Any]):
        meeting_id = payload["id"]
        sync_target = self.config.get("sync_target", "emanager").lower()

        if sync_target == "none":
            logger.info("Synchronizacja chmurowa wyłączona (SYNC_TARGET=none).")
            self.signals.sync_finished.emit(meeting_id, True, "Synchronizacja chmurowa wyłączona.")
            return

        self.signals.sync_started.emit(meeting_id)

        try:
            if sync_target == "emanager":
                success, msg = self._send_to_supabase_emanager(payload)
            elif sync_target == "generic_webhook":
                success, msg = self._send_to_generic_webhook(payload)
            else:
                success, msg = False, f"Nieznany cel synchronizacji: {sync_target}"

            if success:
                logger.info(f"Pomyślnie zsynchronizowano spotkanie {meeting_id}: {msg}")
                self.signals.sync_finished.emit(meeting_id, True, msg)
            else:
                logger.warning(f"Błąd synchronizacji {meeting_id}: {msg}. Zapisywanie do kolejki offline.")
                self._save_to_offline_queue(payload, msg)
        except Exception as e:
            err_msg = f"Nieoczekiwany błąd podczas wysyłki: {e}"
            logger.error(err_msg, exc_info=True)
            self._save_to_offline_queue(payload, err_msg)

    def _start_live_session_worker(self, meeting_id: str, title: str, context_type: str, context_id: Optional[str]):
        """Tworzy wstępny rekord spotkania w Supabase ze statusem 'recording'."""
        sync_target = self.config.get("sync_target", "emanager").lower()
        if sync_target == "none":
            self.signals.live_session_started.emit(meeting_id)
            return

        url = self.config.get("supabase_url", "").rstrip("/")
        key = self.config.get("supabase_key", "")
        if not url or not key:
            self.signals.live_session_started.emit(meeting_id)
            return

        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }

        meeting_record = {
            "id": meeting_id,
            "title": title,
            "duration_seconds": 0,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "recording",
            "context_type": context_type or "general",
            "context_id": context_id,
            "device_name": self.config.get("device_name", "Biuro-Stanowisko-1"),
            "speaker_count": 1,
            "transcript": "",
        }

        try:
            req = urllib.request.Request(
                f"{url}/rest/v1/meetings",
                data=json.dumps(meeting_record).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201, 204):
                    logger.info(f"[LIVE STREAM] Rozpoczęto nową sesję spotkania w Supabase: {meeting_id} (status='recording')")
        except urllib.error.HTTPError as he:
            if he.code == 409:
                logger.info(f"[LIVE STREAM] Sesja spotkania {meeting_id} już istnieje w bazie.")
            else:
                logger.warning(f"[LIVE STREAM] Błąd startu sesji w tabeli meetings ({he.code}): {he.reason}")
        except Exception as e:
            logger.warning(f"[LIVE STREAM] Błąd połączenia podczas startu sesji: {e}")

        self.signals.live_session_started.emit(meeting_id)

    def _append_live_segments_worker(
        self,
        meeting_id: str,
        new_segments: List[Dict[str, Any]],
        full_transcript: str,
        duration_seconds: float,
        speaker_count: int
    ):
        """Wysyła nowe segmenty mowy na żywo do meeting_segments oraz aktualizuje nagłówek spotkania."""
        sync_target = self.config.get("sync_target", "emanager").lower()
        if sync_target == "none":
            return

        url = self.config.get("supabase_url", "").rstrip("/")
        key = self.config.get("supabase_key", "")
        if not url or not key:
            return

        # Dołącz nowe segmenty do bufora pending (ochrona przed utratą przy awariach sieci)
        with self._live_sync_lock:
            if new_segments:
                existing_ids = {s.get("id") for s in self._pending_live_segments if s.get("id")}
                for s in new_segments:
                    sid = s.get("id")
                    if not sid:
                        sid = str(uuid.uuid4())
                        s["id"] = sid
                    if sid not in existing_ids:
                        self._pending_live_segments.append(s)
                        existing_ids.add(sid)
            batch_to_send = list(self._pending_live_segments)

        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal,resolution=merge-duplicates"
        }

        segments_sent_successfully = False
        sent_count = 0

        # 1. Wstaw nowe i ewentualne zaległe segmenty mowy do tabeli meeting_segments
        if batch_to_send:
            rows = []
            for s in batch_to_send:
                st_val = s.get("start")
                en_val = s.get("end")
                row = {
                    "meeting_id": meeting_id,
                    "speaker_name": str(s.get("speaker") or "Mówca"),
                    "start_time": float(st_val if st_val is not None else 0.0),
                    "end_time": float(en_val if en_val is not None else 0.0),
                    "text": str(s.get("text") or "").strip()
                }
                turn_id = s.get("id")
                if turn_id:
                    try:
                        uuid.UUID(str(turn_id))
                        row["id"] = str(turn_id)
                    except (ValueError, TypeError):
                        pass
                rows.append(row)
            try:
                req = urllib.request.Request(
                    f"{url}/rest/v1/meeting_segments",
                    data=json.dumps(rows).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status in (200, 201, 204):
                        logger.info(f"[LIVE STREAM] Pomyślnie wysłano {len(rows)} segmentów do meeting_segments ({meeting_id})")
                        segments_sent_successfully = True
                        sent_count = len(rows)
                        with self._live_sync_lock:
                            sent_ids = {r.get("id") for r in rows if r.get("id")}
                            if sent_ids:
                                self._pending_live_segments = [
                                    p for p in self._pending_live_segments
                                    if p.get("id") not in sent_ids
                                ]
                            else:
                                self._pending_live_segments = []
            except urllib.error.HTTPError as he:
                err_body = ""
                try:
                    err_body = he.read().decode("utf-8")
                except Exception:
                    pass
                logger.error(f"[LIVE STREAM] Błąd HTTP {he.code} wysyłki meeting_segments: {he.reason} - {err_body}")
            except Exception as e:
                logger.warning(f"[LIVE STREAM] Błąd wysyłki meeting_segments: {e}")

        # 2. Zaktualizuj nagłówek spotkania w tabeli meetings (aktualny pełny tekst i czas trwania)
        should_patch_meeting = False
        with self._live_sync_lock:
            if duration_seconds >= self._latest_synced_duration:
                should_patch_meeting = True

        if should_patch_meeting:
            patch_data = {
                "transcript": full_transcript.strip(),
                "duration_seconds": int(duration_seconds),
                "speaker_count": max(1, speaker_count),
                "status": "recording"
            }
            try:
                req = urllib.request.Request(
                    f"{url}/rest/v1/meetings?id=eq.{meeting_id}",
                    data=json.dumps(patch_data).encode("utf-8"),
                    headers=headers,
                    method="PATCH"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status in (200, 204):
                        with self._live_sync_lock:
                            if duration_seconds > self._latest_synced_duration:
                                self._latest_synced_duration = duration_seconds
            except Exception as e:
                logger.warning(f"[LIVE STREAM] Błąd PATCH spotkania meetings ({meeting_id}): {e}")

        if segments_sent_successfully and sent_count > 0:
            self.signals.live_block_synced.emit(meeting_id, sent_count)

    def _finalize_live_session_worker(
        self,
        meeting_id: str,
        final_transcript: str,
        duration_seconds: float,
        audio_path: Optional[str],
        turns: Optional[List[Dict[str, Any]]],
        title: Optional[str]
    ):
        """Finalizuje sesję spotkania w Supabase (status -> 'completed', upload audio, kolejka offline fallback)."""
        sync_target = self.config.get("sync_target", "emanager").lower()
        if sync_target == "none":
            self.signals.live_session_finalized.emit(meeting_id, True, "Synchronizacja wyłączona")
            return

        url = self.config.get("supabase_url", "").rstrip("/")
        key = self.config.get("supabase_key", "")
        if not url or not key:
            self.signals.live_session_finalized.emit(meeting_id, False, "Brak kluczy Supabase w .env")
            return

        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }

        # 1. Upload audio z automatyczną kompresją adaptacyjną (zawsze < 100 MB)
        audio_url = None
        if self.config.get("upload_audio") and audio_path and os.path.exists(audio_path):
            audio_url = self._upload_audio_to_supabase(url, key, meeting_id, audio_path)

        # 2. Zlicz unikalnych mówców
        spk_cnt = 1
        if turns:
            spk_set = set(t.get("speaker", "Mówca") for t in turns if t.get("speaker"))
            spk_cnt = len(spk_set) if spk_set else 1

        # 3. Zaktualizuj rekord spotkania do status='completed'
        patch_data = {
            "transcript": final_transcript.strip(),
            "duration_seconds": int(duration_seconds),
            "status": "completed",
            "speaker_count": spk_cnt,
        }
        if title:
            patch_data["title"] = title
        if audio_url:
            patch_data["audio_url"] = audio_url

        try:
            req = urllib.request.Request(
                f"{url}/rest/v1/meetings?id=eq.{meeting_id}",
                data=json.dumps(patch_data).encode("utf-8"),
                headers=headers,
                method="PATCH"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 204):
                    logger.info(f"[LIVE STREAM] Pomyślnie sfinalizowano spotkanie w Supabase: {meeting_id} (status='completed')")
                    self.signals.live_session_finalized.emit(meeting_id, True, "Spotkanie pomyślnie zakończone i zsynchronizowane!")
                    return
        except Exception as e:
            logger.warning(f"[LIVE STREAM] Błąd finalizacji PATCH spotkania: {e}")

        # Jeśli PATCH nie powiódł się, zapisz do kolejki offline
        segments = []
        for t in (turns or []):
            st_val = t.get("start")
            en_val = t.get("end")
            segments.append({
                "speaker": str(t.get("speaker") or "Mówca"),
                "start": float(st_val if st_val is not None else 0.0),
                "end": float(en_val if en_val is not None else 0.0),
                "text": str(t.get("text") or "").strip()
            })
        full_payload = self._build_payload(
            meeting_id=meeting_id,
            title=title or f"Spotkanie {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            transcript_text=final_transcript,
            segments=segments,
            duration_seconds=duration_seconds,
            audio_path=audio_path,
            context_type="general",
            context_id=None
        )
        self._save_to_offline_queue(full_payload, "Błąd finalizacji sesji na żywo")
        self.signals.live_session_finalized.emit(meeting_id, False, "Zapisano w kolejce offline")

    def _send_to_supabase_emanager(self, payload: Dict[str, Any]) -> tuple[bool, str]:
        """Wysyła spotkanie bezpośrednio do bazy Supabase w EMANAGER.PRO."""
        url = self.config.get("supabase_url", "").rstrip("/")
        key = self.config.get("supabase_key", "")

        if not url or not key:
            return False, "Brak skonfigurowanego SUPABASE_URL lub SUPABASE_KEY w .env"

        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }

        # 1. Opcjonalny upload pliku audio do Storage (bucket: voice-notes)
        audio_url = None
        audio_path = payload.get("audio_path")
        if self.config.get("upload_audio") and audio_path and os.path.exists(audio_path):
            audio_url = self._upload_audio_to_supabase(url, key, payload["id"], audio_path)

        # 2. Rekord spotkania
        meeting_record = {
            "id": payload["id"],
            "title": payload["title"],
            "duration_seconds": int(payload["duration_seconds"]),
            "audio_url": audio_url,
            "created_at": payload["created_at"],
            "status": "completed",
            "context_type": payload.get("context_type", "general"),
            "context_id": payload.get("context_id"),
            "device_name": payload.get("device_name"),
            "speaker_count": payload.get("speaker_count", 1),
            "transcript": payload.get("transcript_text", ""),
        }

        # Próba zapisu do dedykowanej tabeli 'meetings'
        meetings_endpoint = f"{url}/rest/v1/meetings"
        req = urllib.request.Request(
            meetings_endpoint,
            data=json.dumps(meeting_record).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201, 204):
                    self._save_segments_to_supabase(url, headers, payload["id"], payload.get("segments", []))
                    return True, "Zapisano w tabeli spotkań (meetings)"
        except urllib.error.HTTPError as http_err:
            if http_err.code == 409:
                # Rekord o tym ID już istnieje w bazie -> wykonujemy bezpieczną aktualizację (PATCH)
                logger.info(f"Spotkanie {payload['id']} już istnieje w bazie (409 Conflict), aktualizuję rekord metodą PATCH...")
                patch_data = {
                    "title": payload["title"],
                    "transcript": payload.get("transcript_text", ""),
                    "speaker_count": payload.get("speaker_count", 1),
                    "status": "completed",
                }
                if int(payload.get("duration_seconds", 0)) > 0:
                    patch_data["duration_seconds"] = int(payload["duration_seconds"])
                if audio_url:
                    patch_data["audio_url"] = audio_url

                patch_req = urllib.request.Request(
                    f"{meetings_endpoint}?id=eq.{payload['id']}",
                    data=json.dumps(patch_data).encode("utf-8"),
                    headers=headers,
                    method="PATCH"
                )
                try:
                    with urllib.request.urlopen(patch_req, timeout=15) as patch_resp:
                        if patch_resp.status in (200, 204):
                            self._save_segments_to_supabase(url, headers, payload["id"], payload.get("segments", []))
                            return True, "Zaktualizowano spotkanie w tabeli meetings"
                except Exception as patch_err:
                    return False, f"Błąd aktualizacji PATCH spotkania: {patch_err}"

            # Jeśli tabela 'meetings' nie istnieje, wykonujemy fallback do 'voice_notes'
            logger.info(f"Tabela 'meetings' zwróciła {http_err.code}, próba zapisu do tabeli 'voice_notes'...")
            fallback_success, fallback_msg = self._fallback_save_to_voice_notes(url, headers, payload, audio_url)
            if fallback_success:
                return True, fallback_msg
            return False, f"Błąd HTTP {http_err.code}: {http_err.reason}"
        except urllib.error.URLError as url_err:
            return False, f"Błąd połączenia sieciowego: {url_err.reason}"

        return True, "Zsynchronizowano pomyślnie."

    def _save_segments_to_supabase(self, base_url: str, headers: dict, meeting_id: str, segments: List[dict]):
        """Zapisuje listę segmentów wypowiedzi do meeting_segments (najpierw czyści stare, by uniknąć duplikatów)."""
        if not segments:
            return

        # 1. Usuń stare segmenty dla tego meeting_id (jeśli to re-sync po edycji mówców)
        del_req = urllib.request.Request(
            f"{base_url}/rest/v1/meeting_segments?meeting_id=eq.{meeting_id}",
            headers=headers,
            method="DELETE"
        )
        try:
            with urllib.request.urlopen(del_req, timeout=10):
                pass
        except Exception as e:
            logger.debug(f"DELETE meeting_segments: {e}")

        # 2. Wstaw nowe segmenty
        rows = []
        for s in segments:
            st_val = s.get("start")
            en_val = s.get("end")
            row = {
                "meeting_id": meeting_id,
                "speaker_name": str(s.get("speaker") or "Mówca"),
                "start_time": float(st_val if st_val is not None else 0.0),
                "end_time": float(en_val if en_val is not None else 0.0),
                "text": str(s.get("text") or "").strip()
            }
            turn_id = s.get("id")
            if turn_id:
                try:
                    uuid.UUID(str(turn_id))
                    row["id"] = str(turn_id)
                except (ValueError, TypeError):
                    pass
            rows.append(row)

        post_headers = dict(headers)
        post_headers["Prefer"] = "return=minimal,resolution=merge-duplicates"

        req = urllib.request.Request(
            f"{base_url}/rest/v1/meeting_segments",
            data=json.dumps(rows).encode("utf-8"),
            headers=post_headers,
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception as e:
            logger.warning(f"Nie udało się zapisać segmentów do meeting_segments: {e}")


    def _fallback_save_to_voice_notes(self, base_url: str, headers: dict, payload: dict, audio_url: Optional[str]) -> tuple[bool, str]:
        """Zapisuje rekord do istniejącej tabeli 'voice_notes' jako kompatybilny fallback."""
        record = {
            "id": payload["id"],
            "duration_seconds": int(payload["duration_seconds"]),
            "audio_url": audio_url,
            "created_at": payload["created_at"],
            "context_type": payload.get("context_type", "general"),
            "context_label": payload["title"],
            "transcript": payload["transcript_text"],
            "source": "ambient_recorder",
            "tags": ["ambient_meeting", f"speakers_{payload.get('speaker_count', 1)}"]
        }

        req = urllib.request.Request(
            f"{base_url}/rest/v1/voice_notes",
            data=json.dumps(record).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201, 204):
                    return True, "Zapisano w module Notatek Głosowych (voice_notes)"
                return False, f"Status: {resp.status}"
        except Exception as e:
            return False, f"Błąd zapisu do voice_notes: {e}"

    def _compress_audio_for_upload(self, audio_path: str, duration_sec: float = 0.0) -> Optional[str]:
        """
        Kompresuje plik WAV do zoptymalizowanego MP3 mono (16kHz) przy użyciu wbudowanego ffmpeg.
        Dobiera bitrate adaptacyjnie w zależności od czasu trwania, aby plik ZAWSZE zmieścił się
        w limicie 100 MB Supabase Storage (np. 8h @ 24kbps = ~86MB, 2h @ 48kbps = ~43MB).
        Zwraca ścieżkę do tymczasowego pliku MP3, lub None jeśli kompresja się nie powiodła.
        """
        try:
            from recorder.audio.converter import get_embedded_ffmpeg_exe
            ffmpeg_exe = get_embedded_ffmpeg_exe()
            if not ffmpeg_exe or not os.path.exists(ffmpeg_exe):
                logger.warning("[UPLOAD] Brak wbudowanego ffmpeg – nie można skompresować audio.")
                return None

            if duration_sec <= 0 and os.path.exists(audio_path):
                try:
                    import soundfile as sf
                    info = sf.info(audio_path)
                    duration_sec = float(info.duration)
                except Exception:
                    pass

            # Wybór optymalnego bitrate:
            # > 4h (14400s) -> 24 kbps (~10.8 MB/h -> 8h = ~86.4 MB < 100 MB)
            # 2h - 4h -> 32 kbps (~14.4 MB/h -> 4h = ~57.6 MB < 100 MB)
            # <= 2h -> 48 kbps (~21.6 MB/h -> 2h = ~43.2 MB < 100 MB)
            if duration_sec > 14400:
                target_bitrate = "24k"
            elif duration_sec > 7200:
                target_bitrate = "32k"
            else:
                target_bitrate = "48k"

            import sys, subprocess
            tmp_mp3 = audio_path.rsplit(".", 1)[0] + "_upload_tmp.mp3"
            cmd = [
                ffmpeg_exe, "-y",
                "-i", audio_path,
                "-vn",
                "-ac", "1",          # mono
                "-ar", "16000",      # 16kHz
                "-codec:a", "libmp3lame",
                "-b:a", target_bitrate,
                tmp_mp3
            ]
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                timeout=300
            )
            if result.returncode == 0 and os.path.exists(tmp_mp3) and os.path.getsize(tmp_mp3) > 0:
                size_mb = os.path.getsize(tmp_mp3) / (1024 * 1024)
                logger.info(f"[UPLOAD] Skompresowano audio: {os.path.basename(audio_path)} → MP3 {target_bitrate} ({size_mb:.1f} MB, czas: {duration_sec/60:.1f} min)")
                return tmp_mp3
            else:
                logger.warning(f"[UPLOAD] Kompresja ffmpeg nie powiodła się (returncode={result.returncode})")
        except Exception as e:
            logger.warning(f"[UPLOAD] Błąd podczas kompresji audio: {e}")
        return None

    def _upload_audio_to_supabase(self, base_url: str, key: str, meeting_id: str, audio_path: str) -> Optional[str]:
        """
        Wgrywa plik audio do bucketu Storage ('meeting-recordings' lub fallback 'voice-notes').
        Automatycznie kompresuje pliki > 30 MB do MP3 48kbps przed uploadem.
        """
        tmp_compressed: Optional[str] = None
        bucket_name = self.config.get("supabase_bucket") or os.environ.get("SUPABASE_STORAGE_BUCKET") or "meeting-recordings"

        try:
            upload_path = audio_path
            ext = os.path.splitext(audio_path)[1].lstrip(".").lower() or "wav"

            # Sprawdź rozmiar – jeśli > 30MB, kompresuj do MP3
            file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
            if file_size_mb > 30:
                logger.info(f"[UPLOAD] Plik audio {file_size_mb:.1f} MB > 30 MB – automatyczna kompresja do MP3 48kbps...")
                compressed = self._compress_audio_for_upload(audio_path)
                if compressed:
                    tmp_compressed = compressed
                    upload_path = compressed
                    ext = "mp3"
                else:
                    logger.warning("[UPLOAD] Kompresja nie powiodła się – próba uploadu oryginalnego pliku WAV.")

            storage_path = f"meetings/{meeting_id}.{ext}"
            content_type = "audio/mpeg" if ext == "mp3" else "audio/wav"

            with open(upload_path, "rb") as f:
                data = f.read()

            headers = {
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": content_type,
                "x-upsert": "true"
            }

            # Próba uploadu do głównego bucketu (domyślnie meeting-recordings)
            buckets_to_try = [bucket_name]
            if bucket_name != "voice-notes":
                buckets_to_try.append("voice-notes")

            for b in buckets_to_try:
                upload_url = f"{base_url}/storage/v1/object/{b}/{storage_path}"
                try:
                    req = urllib.request.Request(upload_url, data=data, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=180) as resp:
                        if resp.status in (200, 201):
                            logger.info(f"[UPLOAD] Pomyślnie wgrano audio do bucketu '{b}': {storage_path}")
                            return storage_path
                except urllib.error.HTTPError as he:
                    logger.warning(f"[UPLOAD] Bucket '{b}' zwrócił status {he.code}. Próba alternatywna...")
                    continue
                except Exception as ex:
                    logger.warning(f"[UPLOAD] Błąd zapisu do bucketu '{b}': {ex}")
                    continue

        except Exception as e:
            logger.warning(f"Nie udało się wgrać pliku audio do Storage: {e}")
        finally:
            if tmp_compressed and os.path.exists(tmp_compressed):
                try:
                    os.remove(tmp_compressed)
                except Exception:
                    pass
        return None

    def _send_to_generic_webhook(self, payload: Dict[str, Any]) -> tuple[bool, str]:
        """Wysyła znormalizowany JSON do webhooka (n8n / custom CRM)."""
        webhook_url = self.config.get("generic_webhook_url")
        if not webhook_url:
            return False, "Brak GENERIC_WEBHOOK_URL w konfiguracji."

        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201, 202, 204):
                return True, f"Wysłano do webhooka (Status {resp.status})"
            return False, f"Webhook zwrócił status {resp.status}"

    def _save_to_offline_queue(self, payload: Dict[str, Any], reason: str):
        """Zapisuje spotkanie do pliku JSON w kolejce offline."""
        meeting_id = payload["id"]
        queue_file = os.path.join(SYNC_QUEUE_DIR, f"{meeting_id}.json")
        try:
            with open(queue_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info(f"Zapisano spotkanie w kolejce offline: {queue_file}")
            self.signals.offline_queued.emit(meeting_id, f"Zapisano lokalnie w kolejce offline ({reason})")
        except Exception as e:
            logger.error(f"Nie udało się zapisać do kolejki offline: {e}")

    def process_offline_queue_async(self):
        """Przetwarza oczekujące pliki w kolejce offline w osobnym wątku."""
        if self._is_processing_queue:
            return
        threading.Thread(target=self._process_offline_queue_worker, daemon=True).start()

    def _process_offline_queue_worker(self):
        self._is_processing_queue = True
        try:
            files = [f for f in os.listdir(SYNC_QUEUE_DIR) if f.endswith(".json")]
            if not files:
                return

            logger.info(f"Przetwarzanie kolejki offline: {len(files)} plików.")
            for filename in files:
                file_path = os.path.join(SYNC_QUEUE_DIR, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        payload = json.load(f)

                    sync_target = self.config.get("sync_target", "emanager").lower()
                    if sync_target == "emanager":
                        success, _ = self._send_to_supabase_emanager(payload)
                    elif sync_target == "generic_webhook":
                        success, _ = self._send_to_generic_webhook(payload)
                    else:
                        success = False

                    if success:
                        os.remove(file_path)
                        logger.info(f"Usunięto przetworzone spotkanie z kolejki offline: {filename}")
                except Exception as e:
                    logger.warning(f"Błąd podczas ponownej próby wysyłki {filename}: {e}")
        finally:
            self._is_processing_queue = False
