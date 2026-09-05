import os
import sys
from pathlib import Path
from typing import Dict, Optional

# Wyciszenie ostrzeżeń o symlinkach HuggingFace na Windowsie
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# Ścieżki główne
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else str(BASE_DIR)
RECORDINGS_DIR = os.path.join(APP_DIR, "recordings")
TRANSCRIPTIONS_DIR = os.path.join(APP_DIR, "transcriptions")
LOGS_DIR = os.path.join(APP_DIR, "logs")

os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Wersja aplikacji i repozytorium GitHub
APP_VERSION = "0.6.0"
GITHUB_REPO = "igorkozielek/recorder67"

# Parametry audio i VAD
SAMPLE_RATE = 16000  # Wymuszone 16000 Hz dla Silero VAD i Whisper
AUDIO_CHANNELS = 1
DEFAULT_AUTO_PAUSE_SEC = 5.0
VAD_SPEECH_THRESHOLD = 0.42
PRE_SPEECH_BUFFER_CHUNKS = 14  # ~0.45s próbek dźwięku sprzed momentu wykrycia mowy (akustyczny pre-roll)
RMS_SILENCE_THRESHOLD = 0.003

# Stany nagrywania
class SmartRecordState:
    STOPPED = 0
    RECORDING_SPEECH = 1
    RECORDING_SILENCE_COUNTDOWN = 2
    AUTO_PAUSED = 3
    MANUAL_PAUSED = 4


# Źródła nagrywania (Mikrofon, Dźwięk Systemu / Discord / Teams, Tryb Hybrydowy)
class RecordSourceMode:
    MIC_ONLY = "mic_only"          # 🎙️ Tylko mikrofon (biuro / sala)
    SYSTEM_ONLY = "system_only"    # 🎧 Tylko dźwięk systemu / Discord / Teams
    HYBRID_DUAL = "hybrid_dual"    # 🎙️+🎧 Tryb Hybrydowy (Mikrofon + System / 2 ścieżki)


def get_hf_token() -> str:
    """
    Pobiera token HuggingFace ze słownika ustawień użytkownika, pliku .env lub zmiennych środowiskowych.
    """
    # 1. Sprawdzenie ustawień użytkownika
    try:
        token = load_user_settings().get("hf_token", "").strip()
        if token:
            return token
    except Exception:
        pass

    # 2. Sprawdzenie zmiennej środowiskowej
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        return token.strip()

    # 2. Sprawdzenie pliku .env w bieżącym katalogu lub w katalogu głównym projektu
    env_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(sys.executable), ".env"),
        getattr(sys, "_MEIPASS", "") and os.path.join(getattr(sys, "_MEIPASS"), ".env"),
        os.path.join(BASE_DIR, ".env"),
        os.path.join(os.path.dirname(__file__), ".env"),
    ]

    for env_path in env_paths:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("HF_TOKEN="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                return val
                        elif line.startswith("HUGGING_FACE_HUB_TOKEN="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                return val
            except Exception:
                pass

    return ""


# Dostępne modele Faster-Whisper z opisem dla UI
WHISPER_MODELS = {
    "small": {
        "id": "small",
        "name": "small",
        "label": "⚡ small (Szybki / Niski narzut CPU)",
        "desc": "Zalecany na słabsze procesory (niski narzut pamięci ~350MB, b. szybka transkrypcja na żywo)"
    },
    "medium": {
        "id": "medium",
        "name": "medium",
        "label": "⚖️ medium (Zrównoważony)",
        "desc": "Dobra jakość języka polskiego, umiarkowane zużycie CPU/GPU (~900MB RAM)"
    },
    "large-v3-turbo": {
        "id": "large-v3-turbo",
        "name": "large-v3-turbo",
        "label": "🚀 large-v3-turbo (Zalecany / Wysoka jakość)",
        "desc": "Najlepszy stosunek jakości do prędkości (4x szybszy niż standardowy large, ~1.2GB RAM)"
    },
    "large-v3": {
        "id": "large-v3",
        "name": "large-v3",
        "label": "🎯 large-v3 (Maksymalna precyzja)",
        "desc": "Najwyższa dokładność słów i interpunkcji (zalecana dedykowana karta graficzna NVIDIA)"
    },
    "base": {
        "id": "base",
        "name": "base",
        "label": "🪶 base (Ultralekki)",
        "desc": "Minimalne wymagania sprzętowe, mniejsza precyzja dla trudniejszych słów"
    }
}

DEFAULT_WHISPER_MODEL = "large-v3-turbo"


def get_env_variable(key: str, default: str = "") -> str:
    """
    Pobiera zmienną z os.environ lub z pliku .env.
    """
    val = os.environ.get(key)
    if val:
        return val.strip()

    env_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(sys.executable), ".env"),
        getattr(sys, "_MEIPASS", "") and os.path.join(getattr(sys, "_MEIPASS"), ".env"),
        os.path.join(BASE_DIR, ".env"),
        os.path.join(os.path.dirname(__file__), ".env"),
    ]

    for env_path in env_paths:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith(f"{key}="):
                            v = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if v:
                                return v
            except Exception:
                pass
    return default


# Konfiguracja Smart Session Splitting i Transmisji na Żywo (8h Ambient Office)
DEFAULT_SESSION_SPLIT_MINUTES = float(get_env_variable("SESSION_SPLIT_MINUTES", "15.0"))
SESSION_SPLIT_SILENCE_SEC = DEFAULT_SESSION_SPLIT_MINUTES * 60.0  # Domyślnie 15 min ciągłej ciszy
MAX_SESSION_DURATION_SEC = float(get_env_variable("MAX_SESSION_HOURS", "2.0")) * 3600.0  # Max 2h na sesję
LIVE_STREAMING_ENABLED = get_env_variable("LIVE_STREAMING_ENABLED", "true").lower() in ("1", "true", "yes")
DEFAULT_SILENCE_ALERT_MINUTES = float(get_env_variable("SILENCE_ALERT_MINUTES", "5.0"))

# Parametry szybkiej transmisji bloków mowy na żywo do CRM (zamiast czekania 2 minut)
LIVE_BLOCK_MIN_SEC = float(get_env_variable("LIVE_BLOCK_MIN_SEC", "15.0"))          # Szybki podgląd po min. 15s mowy
LIVE_BLOCK_MAX_SEC = float(get_env_variable("LIVE_BLOCK_MAX_SEC", "45.0"))          # Maksymalny czas bloku przed wymuszeniem cięcia na pauzie
LIVE_BLOCK_SILENCE_CUT_SEC = float(get_env_variable("LIVE_BLOCK_SILENCE_CUT_SEC", "1.0"))  # Min. 1.0s ciszy VAD na naturalnym końcu zdania

import json
import time

if getattr(sys, "frozen", False):
    SETTINGS_FILE = os.path.join(os.path.dirname(sys.executable), "user_settings.json")
else:
    SETTINGS_FILE = os.path.join(str(BASE_DIR), "user_settings.json")

_CACHED_USER_SETTINGS = None
_CACHED_SETTINGS_TIME = 0.0


def load_user_settings(force_reload: bool = False) -> dict:
    """
    Wczytuje ustawienia użytkownika z user_settings.json z buforowaniem w pamięci RAM.
    Zapewnia natychmiastowy dostęp bez tysięcy zbędnych odczytów z dysku SSD.
    """
    global _CACHED_USER_SETTINGS, _CACHED_SETTINGS_TIME
    now = time.time()
    if not force_reload and _CACHED_USER_SETTINGS is not None and (now - _CACHED_SETTINGS_TIME < 2.0):
        return dict(_CACHED_USER_SETTINGS)

    defaults = {
        "custom_keywords": get_env_variable("CUSTOM_KEYWORDS", "emanager.pro, EMANAGER.PRO, CRM, AI, Supabase, n8n, Make, webhook, API, LLM, GPT-4, Claude, Gemini, Gemini Vision, Helpdesk, Subiekt GT, Subiekt, faktura proforma, synchronizacja, harmonogram, rejestr zmian, zgłoszenia, zamówienia, matryca uprawnień, QR code"),
        "whisper_beam_size": int(get_env_variable("WHISPER_BEAM_SIZE", "5")),
        "default_whisper_model": get_env_variable("DEFAULT_WHISPER_MODEL", "large-v3-turbo"),
        "hf_token": get_env_variable("HF_TOKEN", ""),
        "device_name": get_env_variable("DEVICE_NAME", "Biuro-Stanowisko-1"),
        "organization_id": get_env_variable("ORGANIZATION_ID", "default_org"),
        "sync_target": get_env_variable("SYNC_TARGET", "emanager"),
        "supabase_url": get_env_variable("SUPABASE_URL", ""),
        "supabase_key": get_env_variable("SUPABASE_KEY", get_env_variable("SUPABASE_PUBLISHABLE_KEY", "")),
        "supabase_bucket": get_env_variable("SUPABASE_STORAGE_BUCKET", "meeting-recordings"),
        "generic_webhook_url": get_env_variable("GENERIC_WEBHOOK_URL", ""),
        "auto_cloud_sync": get_env_variable("AUTO_CLOUD_SYNC", "true").lower() in ("1", "true", "yes"),
        "sync_upload_audio": get_env_variable("SYNC_UPLOAD_AUDIO", "false").lower() in ("1", "true", "yes"),
        "vad_speech_threshold": float(get_env_variable("VAD_SPEECH_THRESHOLD", "0.42")),
        "system_vad_speech_threshold": float(get_env_variable("SYSTEM_VAD_SPEECH_THRESHOLD", "0.42")),
        "record_source_mode": get_env_variable("RECORD_SOURCE_MODE", RecordSourceMode.HYBRID_DUAL),
        "loopback_device_index": get_env_variable("LOOPBACK_DEVICE_INDEX", ""),
        "target_app_filter": get_env_variable("TARGET_APP_FILTER", ""),
        "auto_pause_sec": float(get_env_variable("AUTO_PAUSE_SEC", "5.0")),
        "session_split_silence_sec": float(get_env_variable("SESSION_SPLIT_SILENCE_SEC", "900.0")),  # 15 min
        "silence_alert_minutes": float(get_env_variable("SILENCE_ALERT_MINUTES", "5.0")),  # 5 min ostrzeżenie strażnika ciszy
        "timestamp_format": get_env_variable("TIMESTAMP_FORMAT", "offset_only"),
        "preview_order": get_env_variable("PREVIEW_ORDER", "newest_first"),
        "auto_scroll_chronological": get_env_variable("AUTO_SCROLL_CHRONOLOGICAL", "true").lower() in ("1", "true", "yes"),
        "check_prereleases": True,
        "auto_check_updates_startup": True,
        "adaptive_beam_size": False,
        "theme": get_env_variable("APP_THEME", "classic_dark"),
        "font_size": int(get_env_variable("TRANSCRIPT_FONT_SIZE", "13")),
        "always_on_top": get_env_variable("ALWAYS_ON_TOP", "false").lower() in ("1", "true", "yes"),
        "minimize_to_tray_on_close": get_env_variable("MINIMIZE_TO_TRAY_ON_CLOSE", "false").lower() in ("1", "true", "yes"),
        "window_geometry": get_env_variable("WINDOW_GEOMETRY", ""),
    }

    settings_paths = [
        SETTINGS_FILE,
        os.path.join(BASE_DIR, "user_settings.json"),
        os.path.join(os.path.dirname(sys.executable), "user_settings.json")
    ]
    for p in settings_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        merged = dict(defaults)
                        merged.update(data)
                        _CACHED_USER_SETTINGS = dict(merged)
                        _CACHED_SETTINGS_TIME = now
                        return merged
            except Exception as e:
                import logging
                logging.getLogger("recorder").warning(f"Błąd odczytu pliku ustawień '{p}': {e}")

    _CACHED_USER_SETTINGS = dict(defaults)
    _CACHED_SETTINGS_TIME = now
    return defaults


def save_user_settings(settings: dict) -> bool:
    """Zapisuje słownik ustawień do user_settings.json i odświeża bufor pamięci."""
    global _CACHED_USER_SETTINGS, _CACHED_SETTINGS_TIME
    try:
        cur = load_user_settings(force_reload=True)
        cur.update(settings)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=2, ensure_ascii=False)
        _CACHED_USER_SETTINGS = dict(cur)
        _CACHED_SETTINGS_TIME = time.time()
        return True
    except Exception as e:
        if sys.stderr:
            print(f"Błąd zapisu user_settings.json: {e}", file=sys.stderr)
        return False


def get_timestamp_format() -> str:
    """Zwraca wybrany format znacznika czasu (offset_only / clock_only / hybrid)."""
    return str(load_user_settings().get("timestamp_format", "offset_only"))


def get_custom_keywords() -> str:
    """Zwraca zdefiniowany w ustawieniach słownik słów branżowych / nazw własnych."""
    st = load_user_settings()
    return str(st.get("custom_keywords", "")).strip()


def get_beam_size() -> int:
    """Zwraca rozmiar wiązki (beam search) dla Whispera."""
    st = load_user_settings()
    try:
        return max(1, min(10, int(st.get("whisper_beam_size", 5))))
    except Exception:
        return 5


def is_adaptive_beam_size() -> bool:
    """Zwraca czy adaptacyjny dobór beam_size (bieg turbo przy zatorach w kolejce) jest włączony."""
    st = load_user_settings()
    return bool(st.get("adaptive_beam_size", False))


def get_device_name() -> str:
    """Zwraca nazwę stanowiska komputerowego."""
    st = load_user_settings()
    return str(st.get("device_name", "Biuro-Stanowisko-1")).strip()


def get_vad_speech_threshold() -> float:
    """Zwraca próg czułości wykrywania mowy Silero VAD dla mikrofonu."""
    st = load_user_settings()
    try:
        return max(0.1, min(0.9, float(st.get("vad_speech_threshold", 0.42))))
    except Exception:
        return 0.42


def get_system_vad_speech_threshold() -> float:
    """Zwraca próg czułości wykrywania mowy Silero VAD dla dźwięku systemu/Discorda."""
    st = load_user_settings()
    try:
        return max(0.1, min(0.9, float(st.get("system_vad_speech_threshold", 0.42))))
    except Exception:
        return 0.42


def get_record_source_mode() -> str:
    """Zwraca aktywny tryb źródła dźwięku (mic_only, system_only, hybrid_dual)."""
    st = load_user_settings()
    mode = str(st.get("record_source_mode", RecordSourceMode.HYBRID_DUAL)).strip()
    if mode in (RecordSourceMode.MIC_ONLY, RecordSourceMode.SYSTEM_ONLY, RecordSourceMode.HYBRID_DUAL):
        return mode
    return RecordSourceMode.HYBRID_DUAL


def get_loopback_device_index() -> str:
    """Zwraca zapisany identyfikator/indeks urządzenia WASAPI Loopback."""
    st = load_user_settings()
    return str(st.get("loopback_device_index", "")).strip()


def get_target_app_filter() -> str:
    """Zwraca nazwę/filtr docelowej aplikacji z dźwiękiem (np. Discord.exe)."""
    st = load_user_settings()
    return str(st.get("target_app_filter", "")).strip()


def get_session_split_silence_sec() -> float:
    """Zwraca czas ciągłej ciszy wymagany do automatycznego podziału na nową sesję (sekundy)."""
    st = load_user_settings()
    try:
        return float(st.get("session_split_silence_sec", 900.0))
    except Exception:
        return 900.0


def get_silence_alert_minutes() -> float:
    """Zwraca czas ciszy wymagany do wyświetlenia ostrzeżenia o braku głosu (minuty). 0.0 oznacza wyłączone."""
    st = load_user_settings()
    try:
        return float(st.get("silence_alert_minutes", DEFAULT_SILENCE_ALERT_MINUTES))
    except Exception:
        return DEFAULT_SILENCE_ALERT_MINUTES


def get_silence_alert_seconds() -> float:
    """Zwraca czas ciszy wymagany do wyświetlenia ostrzeżenia o braku głosu (sekundy). 0.0 oznacza wyłączone."""
    mins = get_silence_alert_minutes()
    return max(0.0, mins * 60.0)


def is_silence_alert_enabled() -> bool:
    """Sprawdza, czy funkcja strażnika ciszy jest aktywna."""
    return get_silence_alert_seconds() > 0.0


def get_preview_order() -> str:
    """Zwraca preferowaną kolejność wypowiedzi w oknie podglądu ('newest_first' lub 'chronological')."""
    st = load_user_settings()
    order = str(st.get("preview_order", "newest_first")).strip()
    if order in ("newest_first", "chronological"):
        return order
    return "newest_first"


def is_auto_scroll_chronological() -> bool:
    """Zwraca czy w trybie chronologicznym podgląd ma automatycznie przewijać do najnowszych wypowiedzi."""
    st = load_user_settings()
    return bool(st.get("auto_scroll_chronological", True))


def get_theme() -> str:
    """Zwraca aktywny identyfikator motywu (classic_dark, classic_light, emanager_dark, emanager_light)."""
    st = load_user_settings()
    return str(st.get("theme", "classic_dark")).strip() or "classic_dark"


THEME_SPEAKER_COLORS: Dict[str, Dict[str, str]] = {
    "classic_dark": {"mic": "#4cc9f0", "system": "#a370f7"},
    "classic_light": {"mic": "#0369a1", "system": "#7c3aed"},
    "emanager_dark": {"mic": "#ff6b6b", "system": "#38bdf8"},
    "emanager_light": {"mic": "#b91c1c", "system": "#1d4ed8"},
}


def get_speaker_colors(theme_id: Optional[str] = None) -> Dict[str, str]:
    if theme_id and theme_id in THEME_SPEAKER_COLORS:
        return THEME_SPEAKER_COLORS[theme_id]
    return THEME_SPEAKER_COLORS["classic_dark"]


def get_font_size() -> int:
    """Zwraca rozmiar czcionki podglądu transkrypcji (11-17 px)."""
    st = load_user_settings()
    try:
        val = int(st.get("font_size", st.get("transcript_font_size", 13)))
        return max(11, min(17, val))
    except Exception:
        return 13


get_transcript_font_size = get_font_size


def is_always_on_top() -> bool:
    """Zwraca czy okno aplikacji powinno pozostawać zawsze na wierzchu."""
    st = load_user_settings()
    return bool(st.get("always_on_top", False))


def is_minimize_to_tray_on_close() -> bool:
    """Zwraca czy kliknięcie [X] minimalizuje okno do zasobnika systemowego."""
    st = load_user_settings()
    return bool(st.get("minimize_to_tray_on_close", False))


def get_window_geometry() -> str:
    """Zwraca zapisany stan geometrii okna (hex string) lub pusty ciąg."""
    st = load_user_settings()
    return str(st.get("window_geometry", "")).strip()


def set_window_geometry(geom: str) -> bool:
    """Zapisuje zakodowaną geometrię okna w user_settings.json."""
    return save_user_settings({"window_geometry": str(geom).strip()})


def get_default_beam_size() -> int:
    """Kompatybilność wsteczna: zwraca get_beam_size()."""
    return get_beam_size()


DEFAULT_BEAM_SIZE = get_beam_size()

# Domyślny słownik początkowy dla Whispera (emanager.pro, CRM z AI, automatyzacje n8n, biznes, architektura)
DEFAULT_INITIAL_PROMPT = (
    "emanager.pro, EMANAGER.PRO, CRM, AI, Supabase, n8n, Make, webhook, API, LLM, GPT-4, Claude, Gemini, "
    "Gemini Vision, Lovable, React, Helpdesk, Subiekt GT, Subiekt, faktura proforma, zamówienia, zgłoszenia, "
    "harmonogram, kategorie, dyplomy, matryca uprawnień, recepcja, check-in, QR code, CSV, oświetleniowiec, "
    "synchronizacja, rejestr zmian, diaryzacja, transkrypcja, procesy biznesowe, architektura wzrostu."
)


def get_full_initial_prompt(extra_context: str = "") -> str:
    """
    Tworzy zoptymalizowany initial_prompt dla Whispera, łącząc słownik bazowy,
    słownik branżowy użytkownika oraz ewentualny kontekst ostatnich zdań.
    """
    kw = get_custom_keywords()
    prompt = DEFAULT_INITIAL_PROMPT
    if kw:
        prompt = f"{prompt}, {kw}"
    if extra_context and len(extra_context.strip()) > 3:
        prompt = f"{prompt} {extra_context.strip()[-150:]}"
    return prompt


# Konfiguracja Cloud Sync / Multi-Tenant / EMANAGER.PRO
SYNC_QUEUE_DIR = os.path.join(TRANSCRIPTIONS_DIR, "sync_queue")
os.makedirs(SYNC_QUEUE_DIR, exist_ok=True)


def get_cloud_sync_config() -> dict:
    """
    Zwraca aktualną konfigurację integracji chmurowej (z priorytetem dla user_settings.json).
    """
    st = load_user_settings()
    return {
        "sync_target": st.get("sync_target") or get_env_variable("SYNC_TARGET", "emanager"),
        "supabase_url": st.get("supabase_url") or get_env_variable("SUPABASE_URL", ""),
        "supabase_key": st.get("supabase_key") or get_env_variable("SUPABASE_KEY", get_env_variable("SUPABASE_PUBLISHABLE_KEY", "")),
        "supabase_bucket": st.get("supabase_bucket") or get_env_variable("SUPABASE_STORAGE_BUCKET", "meeting-recordings"),
        "device_name": st.get("device_name") or get_env_variable("DEVICE_NAME", "Biuro-Stanowisko-1"),
        "organization_id": st.get("organization_id") or get_env_variable("ORGANIZATION_ID", "default_org"),
        "auto_sync": bool(st.get("auto_cloud_sync", True)),
        "generic_webhook_url": st.get("generic_webhook_url") or get_env_variable("GENERIC_WEBHOOK_URL", ""),
        "upload_audio": bool(st.get("sync_upload_audio", False)),
        "live_streaming": LIVE_STREAMING_ENABLED,
        "session_split_silence_sec": get_session_split_silence_sec(),
        "max_session_duration_sec": MAX_SESSION_DURATION_SEC,
    }


def is_auto_check_updates_startup() -> bool:
    """Sprawdza, czy włączone jest ciche sprawdzanie aktualizacji przy uruchomieniu programu."""
    st = load_user_settings()
    return bool(st.get("auto_check_updates_startup", True))


def get_hardware_acceleration_info() -> dict:
    """
    Automatycznie wykrywa dostępne zasoby sprzętowe (CUDA GPU vs CPU)
    i dobiera optymalny typ obliczeń (compute_type) oraz liczbę wątków.
    """
    cuda_available = False
    gpu_name = ""
    try:
        import torch
        if torch.cuda.is_available():
            cuda_available = True
            gpu_name = torch.cuda.get_device_name(0)
    except Exception:
        cuda_available = False

    if cuda_available:
        return {
            "device": "cuda",
            "compute_type": "float16",
            "cpu_threads": 4,
            "is_cuda": True,
            "badge_text": f"🚀 Akceleracja: NVIDIA GPU ({gpu_name}) • float16",
            "summary": "GPU (CUDA float16)"
        }
    else:
        # Na CPU dobieramy liczbę wątków z zachowaniem zapasu na UI i Silero VAD
        total_cores = os.cpu_count() or 4
        # Np. dla 6 rdzeni (i5-8500) -> 4 wątki; dla 4 rdzeni -> 3 wątki; min 1
        safe_threads = max(1, min(6, total_cores - 1 if total_cores > 2 else total_cores))
        return {
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": safe_threads,
            "is_cuda": False,
            "badge_text": f"💻 Akceleracja: CPU ({safe_threads} wątków, int8 AVX)",
            "summary": f"CPU (int8 - {safe_threads} thr)"
        }


# Opcje wyboru liczby osób dla PyAnnote (Limity Max vs Dokładna liczba)
SPEAKER_COUNT_OPTIONS = [
    ("Auto (Bez limitu / Dowolna liczba osób)", {}),
    ("Rozmowa 2-3 osoby (Zalecane dla małych narad)", {"min_speakers": 2, "max_speakers": 3}),
    ("Rozmowa 2-4 osoby", {"min_speakers": 2, "max_speakers": 4}),
    ("Spotkanie zespołowe 4-7 osób", {"min_speakers": 4, "max_speakers": 7}),
    ("Duże spotkanie biurowe 6-10 osób", {"min_speakers": 6, "max_speakers": 10}),
    ("Maksymalnie 2 osoby (Dialog)", {"max_speakers": 2}),
    ("Maksymalnie 3 osoby", {"max_speakers": 3}),
    ("Maksymalnie 4 osoby", {"max_speakers": 4}),
    ("Maksymalnie 5 osób", {"max_speakers": 5}),
    ("Maksymalnie 8 osób", {"max_speakers": 8}),
    ("Maksymalnie 10 osób", {"max_speakers": 10}),
    ("Dokładnie 1 osoba (Monolog)", {"num_speakers": 1}),
    ("Dokładnie 2 osoby", {"num_speakers": 2}),
    ("Dokładnie 3 osoby", {"num_speakers": 3}),
    ("Dokładnie 4 osoby", {"num_speakers": 4}),
    ("Dokładnie 5 osób", {"num_speakers": 5}),
    ("Dokładnie 6 osób", {"num_speakers": 6}),
    ("Dokładnie 8 osób", {"num_speakers": 8}),
    ("Dokładnie 10 osób", {"num_speakers": 10}),
]



def get_recommended_profile() -> dict:
    """
    Analizuje konfigurację sprzętową i zwraca rekomendowany model oraz uzasadnienie.
    """
    hw = get_hardware_acceleration_info()
    cores = os.cpu_count() or 4

    if hw["is_cuda"]:
        return {
            "recommended_model": "large-v3-turbo",
            "title": "Wykryto kartę NVIDIA (CUDA)",
            "message": (
                f"Wykryto akcelerację GPU: {hw['badge_text']}.\n\n"
                "Ustawiono rekomendowany model: 'large-v3-turbo' (float16),\n"
                "który zapewnia najwyższą precyzję transkrypcji języka polskiego przy błyskawicznym czasie działania."
            )
        }
    else:
        # Maszyna CPU
        if cores >= 4:
            rec_model = "large-v3-turbo"
            return {
                "recommended_model": rec_model,
                "title": "Wykryto wielordzeniowy procesor CPU",
                "message": (
                    f"Wykryto procesor CPU z {cores} wątkami/rdzeniami.\n\n"
                    f"Ustawiono zoptymalizowany model: '{rec_model}' w trybie int8 ({hw['cpu_threads']} wątki robocze).\n\n"
                    "Zapewnia on najwyższą precyzję języka polskiego i poprawność trudnych zwrotów."
                )
            }
        else:
            rec_model = "small"
            return {
                "recommended_model": rec_model,
                "title": "Wykryto procesor CPU",
                "message": (
                    f"Wykryto procesor CPU z {cores} wątkami.\n\n"
                    f"Ustawiono lekki model: '{rec_model}' (int8) dla zachowania optymalnej płynności."
                )
            }



