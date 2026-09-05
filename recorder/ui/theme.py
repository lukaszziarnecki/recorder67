"""
Centralny silnik motywów i personalizacji wizualnej dla aplikacji Recorder67.

Obsługuje:
1. Rejestr 4 motywów wizualnych (classic_dark, classic_light, emanager_dark, emanager_light).
2. Tokenizowany generator arkuszy stylów QSS (generate_theme_qss).
3. Konfigurację palety QPalette (setup_theme_palette) ze stylem Fusion.
4. Mapowanie kolorów mówców o wysokim kontraście WCAG (get_speaker_colors).
5. Załadowanie i rejestrację czcionki Saira z fallbackiem do Segoe UI.
6. Integrację z natywnym paskiem tytułu Windows 10/11 DWM (set_window_titlebar_theme).
7. Wsteczną kompatybilność z DARK_THEME_QSS oraz setup_dark_palette.
"""

import os
import sys
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from recorder.config import THEME_SPEAKER_COLORS, get_speaker_colors

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette, QColor, QFont, QFontDatabase, QGuiApplication
    from PySide6.QtWidgets import QApplication, QWidget
except ImportError:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPalette, QColor, QFont, QFontDatabase, QGuiApplication
    from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger("recorder.ui.theme")


# ============================================================================
# 1. STRUKTURA DANYCH I REJESTR MOTYWÓW
# ============================================================================

@dataclass(frozen=True)
class ThemeDefinition:
    """Definicja tokenów kolorystycznych i typograficznych motywu."""
    id: str
    name: str
    description: str
    is_dark: bool
    font_family_default: str

    # Swatche dla kart wyboru motywu (AppearanceTab)
    swatch_bg: str
    swatch_accent: str
    swatch_text: str

    # Tła bazowe (bg_window i bg_app jako tożsame aliasy)
    bg_window: str
    bg_app: str
    bg_surface: str
    bg_elevated: str
    bg_input: str
    bg_hover: str

    # Typografia
    text_primary: str
    text_secondary: str
    text_muted: str
    text_on_accent: str

    # Akcenty i obramowania (border, border_strong, border_subtle, border_focus)
    border: str
    border_strong: str
    border_subtle: str
    border_focus: str
    accent: str
    accent_hover: str
    accent_pressed: str

    # Przyciski standardowe i akcji
    btn_default_bg: str
    btn_default_hover: str
    btn_default_text: str
    btn_primary_bg: str
    btn_primary_hover: str
    btn_primary_text: str
    btn_start_bg: str
    btn_start_hover: str
    btn_start_text: str
    btn_pause_bg: str
    btn_pause_hover: str
    btn_pause_text: str
    btn_resume_bg: str
    btn_resume_hover: str
    btn_resume_text: str
    btn_stop_bg: str
    btn_stop_hover: str
    btn_stop_text: str

    # Etykiety statusu nagrywania
    status_stopped_bg: str
    status_stopped_text: str
    status_speech_bg: str
    status_speech_text: str
    status_countdown_bg: str
    status_countdown_text: str
    status_autopaused_bg: str
    status_autopaused_text: str
    status_manualpaused_bg: str
    status_manualpaused_text: str

    # Kolory mówców w transkrypcji (WCAG AA/AAA)
    speaker_mic_color: str
    speaker_system_color: str
    speaker_mic: str
    speaker_system: str

    # Pasek przewijania
    scrollbar_track: str
    scrollbar_handle: str
    scrollbar_handle_hover: str

    # Zaznaczenie
    selection_bg: str
    selection_text: str


DEFAULT_THEME_ID = "classic_dark"

THEMES: Dict[str, ThemeDefinition] = {
    # 1. Classic Dark (Domyślny)
    "classic_dark": ThemeDefinition(
        id="classic_dark",
        name="Classic Dark",
        description="Grafitowy motyw z turkusowymi akcentami (Segoe UI)",
        is_dark=True,
        font_family_default="'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif",
        swatch_bg="#111216",
        swatch_accent="#4cc9f0",
        swatch_text="#edf2f4",
        bg_window="#111216",
        bg_app="#111216",
        bg_surface="#171820",
        bg_elevated="#1e1e2f",
        bg_input="#181824",
        bg_hover="#2b2e3d",
        text_primary="#edf2f4",
        text_secondary="#8d99ae",
        text_muted="#6c757d",
        text_on_accent="#111216",
        border="#2b2d42",
        border_strong="#2b2d42",
        border_subtle="#212430",
        border_focus="#4cc9f0",
        accent="#4cc9f0",
        accent_hover="#38bdf8",
        accent_pressed="#0284c7",
        btn_default_bg="#222533",
        btn_default_hover="#33374c",
        btn_default_text="#edf2f4",
        btn_primary_bg="#10b981",
        btn_primary_hover="#059669",
        btn_primary_text="#ffffff",
        btn_start_bg="#dc2626",
        btn_start_hover="#ef4444",
        btn_start_text="#ffffff",
        btn_pause_bg="#d97706",
        btn_pause_hover="#f59e0b",
        btn_pause_text="#ffffff",
        btn_resume_bg="#059669",
        btn_resume_hover="#10b981",
        btn_resume_text="#ffffff",
        btn_stop_bg="#4b5563",
        btn_stop_hover="#6b7280",
        btn_stop_text="#ffffff",
        status_stopped_bg="#272a38",
        status_stopped_text="#8d99ae",
        status_speech_bg="#10b981",
        status_speech_text="#ffffff",
        status_countdown_bg="#0284c7",
        status_countdown_text="#ffffff",
        status_autopaused_bg="#f59e0b",
        status_autopaused_text="#ffffff",
        status_manualpaused_bg="#6b7280",
        status_manualpaused_text="#ffffff",
        speaker_mic_color="#4cc9f0",
        speaker_system_color="#a370f7",
        speaker_mic="#4cc9f0",
        speaker_system="#a370f7",
        scrollbar_track="#111216",
        scrollbar_handle="#2b2e3d",
        scrollbar_handle_hover="#3b4055",
        selection_bg="#272a38",
        selection_text="#4cc9f0",
    ),

    # 2. Classic Light
    "classic_light": ThemeDefinition(
        id="classic_light",
        name="Classic Light",
        description="Jasny motyw slate/white z błękitnym kobaltem (Segoe UI)",
        is_dark=False,
        font_family_default="'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif",
        swatch_bg="#ffffff",
        swatch_accent="#0284c7",
        swatch_text="#0f172a",
        bg_window="#f8fafc",
        bg_app="#f8fafc",
        bg_surface="#ffffff",
        bg_elevated="#f1f5f9",
        bg_input="#ffffff",
        bg_hover="#e2e8f0",
        text_primary="#0f172a",
        text_secondary="#475569",
        text_muted="#94a3b8",
        text_on_accent="#ffffff",
        border="#cbd5e1",
        border_strong="#94a3b8",
        border_subtle="#f1f5f9",
        border_focus="#0284c7",
        accent="#0284c7",
        accent_hover="#0369a1",
        accent_pressed="#075985",
        btn_default_bg="#e2e8f0",
        btn_default_hover="#cbd5e1",
        btn_default_text="#0f172a",
        btn_primary_bg="#059669",
        btn_primary_hover="#047857",
        btn_primary_text="#ffffff",
        btn_start_bg="#dc2626",
        btn_start_hover="#b91c1c",
        btn_start_text="#ffffff",
        btn_pause_bg="#d97706",
        btn_pause_hover="#b45309",
        btn_pause_text="#ffffff",
        btn_resume_bg="#059669",
        btn_resume_hover="#047857",
        btn_resume_text="#ffffff",
        btn_stop_bg="#475569",
        btn_stop_hover="#334155",
        btn_stop_text="#ffffff",
        status_stopped_bg="#e2e8f0",
        status_stopped_text="#475569",
        status_speech_bg="#059669",
        status_speech_text="#ffffff",
        status_countdown_bg="#0284c7",
        status_countdown_text="#ffffff",
        status_autopaused_bg="#d97706",
        status_autopaused_text="#ffffff",
        status_manualpaused_bg="#64748b",
        status_manualpaused_text="#ffffff",
        speaker_mic_color="#0369a1",
        speaker_system_color="#7c3aed",
        speaker_mic="#0369a1",
        speaker_system="#7c3aed",
        scrollbar_track="#f8fafc",
        scrollbar_handle="#cbd5e1",
        scrollbar_handle_hover="#94a3b8",
        selection_bg="#e0f2fe",
        selection_text="#0369a1",
    ),

    # 3. EMANAGER.PRO Dark
    "emanager_dark": ThemeDefinition(
        id="emanager_dark",
        name="EMANAGER.PRO Dark",
        description="Głęboka czerń obsydianu z karminową czerwienią (Saira)",
        is_dark=True,
        font_family_default="'Saira', 'Segoe UI', -apple-system, sans-serif",
        swatch_bg="#0c0e12",
        swatch_accent="#ee2b2b",
        swatch_text="#fafafa",
        bg_window="#0c0e12",
        bg_app="#0c0e12",
        bg_surface="#14171f",
        bg_elevated="#1c202b",
        bg_input="#161922",
        bg_hover="#242938",
        text_primary="#fafafa",
        text_secondary="#9aa0b4",
        text_muted="#656c80",
        text_on_accent="#ffffff",
        border="#292f42",
        border_strong="#292f42",
        border_subtle="#1e222f",
        border_focus="#ee2b2b",
        accent="#ee2b2b",
        accent_hover="#ff4d4d",
        accent_pressed="#dc2626",
        btn_default_bg="#1d212d",
        btn_default_hover="#282e3f",
        btn_default_text="#fafafa",
        btn_primary_bg="#ee2b2b",
        btn_primary_hover="#dc2626",
        btn_primary_text="#ffffff",
        btn_start_bg="#ee2b2b",
        btn_start_hover="#ff4d4d",
        btn_start_text="#ffffff",
        btn_pause_bg="#d97706",
        btn_pause_hover="#f59e0b",
        btn_pause_text="#ffffff",
        btn_resume_bg="#059669",
        btn_resume_hover="#10b981",
        btn_resume_text="#ffffff",
        btn_stop_bg="#3b4256",
        btn_stop_hover="#4d566f",
        btn_stop_text="#ffffff",
        status_stopped_bg="#222634",
        status_stopped_text="#8b92a5",
        status_speech_bg="#10b981",
        status_speech_text="#ffffff",
        status_countdown_bg="#0284c7",
        status_countdown_text="#ffffff",
        status_autopaused_bg="#f59e0b",
        status_autopaused_text="#ffffff",
        status_manualpaused_bg="#6b7280",
        status_manualpaused_text="#ffffff",
        speaker_mic_color="#ff6b6b",
        speaker_system_color="#38bdf8",
        speaker_mic="#ff6b6b",
        speaker_system="#38bdf8",
        scrollbar_track="#0c0e12",
        scrollbar_handle="#242938",
        scrollbar_handle_hover="#353d52",
        selection_bg="#2d1b22",
        selection_text="#ff6b6b",
    ),

    # 4. EMANAGER.PRO Light
    "emanager_light": ThemeDefinition(
        id="emanager_light",
        name="EMANAGER.PRO Light",
        description="Czysta biel biznesowa z karminowym akcentem (Saira)",
        is_dark=False,
        font_family_default="'Saira', 'Segoe UI', -apple-system, sans-serif",
        swatch_bg="#ffffff",
        swatch_accent="#dc2626",
        swatch_text="#14171f",
        bg_window="#fafafa",
        bg_app="#fafafa",
        bg_surface="#ffffff",
        bg_elevated="#f0f2f5",
        bg_input="#ffffff",
        bg_hover="#e5e7eb",
        text_primary="#14171f",
        text_secondary="#4b5563",
        text_muted="#9ca3af",
        text_on_accent="#ffffff",
        border="#d1d5db",
        border_strong="#9ca3af",
        border_subtle="#f3f4f6",
        border_focus="#dc2626",
        accent="#dc2626",
        accent_hover="#b91c1c",
        accent_pressed="#991b1b",
        btn_default_bg="#f0f2f5",
        btn_default_hover="#e5e7eb",
        btn_default_text="#14171f",
        btn_primary_bg="#dc2626",
        btn_primary_hover="#b91c1c",
        btn_primary_text="#ffffff",
        btn_start_bg="#dc2626",
        btn_start_hover="#b91c1c",
        btn_start_text="#ffffff",
        btn_pause_bg="#d97706",
        btn_pause_hover="#b45309",
        btn_pause_text="#ffffff",
        btn_resume_bg="#059669",
        btn_resume_hover="#047857",
        btn_resume_text="#ffffff",
        btn_stop_bg="#4b5563",
        btn_stop_hover="#374151",
        btn_stop_text="#ffffff",
        status_stopped_bg="#e5e7eb",
        status_stopped_text="#4b5563",
        status_speech_bg="#059669",
        status_speech_text="#ffffff",
        status_countdown_bg="#0284c7",
        status_countdown_text="#ffffff",
        status_autopaused_bg="#d97706",
        status_autopaused_text="#ffffff",
        status_manualpaused_bg="#6b7280",
        status_manualpaused_text="#ffffff",
        speaker_mic_color="#b91c1c",
        speaker_system_color="#1d4ed8",
        speaker_mic="#b91c1c",
        speaker_system="#1d4ed8",
        scrollbar_track="#fafafa",
        scrollbar_handle="#d1d5db",
        scrollbar_handle_hover="#9ca3af",
        selection_bg="#fee2e2",
        selection_text="#b91c1c",
    ),
}


def get_theme(theme_id: Optional[str]) -> ThemeDefinition:
    """Zwraca definicję motywu o danym ID z bezpiecznym fallbackiem do classic_dark."""
    if theme_id and theme_id in THEMES:
        return THEMES[theme_id]
    return THEMES[DEFAULT_THEME_ID]


def get_available_themes() -> List[ThemeDefinition]:
    """Zwraca listę wszystkich zarejestrowanych motywów."""
    return list(THEMES.values())


# ============================================================================
# 2. ZARZĄDZANIE CZCIONKAMI (SAIRA + FALLBACK SEGOE UI)
# ============================================================================

_fonts_registered: bool = False
_saira_available: bool = False
_saira_family: str = "Saira"
_registered_fonts_dir: Optional[str] = None

FALLBACK_FONT_FAMILY = "Segoe UI"


def get_fonts_dir() -> str:
    """
    Zwraca bezwzględną ścieżkę do katalogu czcionek z uwzględnieniem
    zarówno trybu developerskiego, jak i spakowanego PyInstallera.
    """
    # 1. PyInstaller frozen onefile/onedir bundle
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = os.path.join(sys._MEIPASS, "recorder", "resources", "fonts")
        if os.path.isdir(candidate):
            return candidate
        candidate_alt = os.path.join(sys._MEIPASS, "resources", "fonts")
        if os.path.isdir(candidate_alt):
            return candidate_alt

    # 2. Względem modułu (recorder/ui/theme.py -> recorder/resources/fonts)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(base_dir, "resources", "fonts")
    if os.path.isdir(candidate) or os.path.exists(candidate):
        return candidate

    # 3. Względem głównego katalogu repozytorium
    root_dir = os.path.dirname(base_dir)
    candidate_root = os.path.join(root_dir, "recorder", "resources", "fonts")
    if os.path.isdir(candidate_root) or os.path.exists(candidate_root):
        return candidate_root

    return candidate


def register_bundled_fonts(fonts_dir: Optional[str] = None, force: bool = False) -> bool:
    """
    Wykrywa i rejestruje dołączoną czcionkę SIL OFL Saira w QFontDatabase.
    Funkcja jest idempotentna – rejestruje raz w cyklu życia procesu aplikacji.

    Zwraca:
        bool: True jeśli czcionka została pomyślnie zarejestrowana lub była już obecna.
              False jeśli pliki czcionki nie istnieją lub brak aktywnej QGuiApplication.
    """
    global _fonts_registered, _saira_available, _saira_family, _registered_fonts_dir

    target_dir = fonts_dir or get_fonts_dir()
    if not os.path.exists(target_dir):
        return False

    if _fonts_registered and not force and (fonts_dir is None or fonts_dir == _registered_fonts_dir):
        return _saira_available

    # Ochrona środowisk headless: QFontDatabase wymaga instancji QGuiApplication
    if QGuiApplication.instance() is None:
        return False

    candidate_files = [
        "Saira[wdth,wght].ttf",
        "Saira-Regular.ttf",
        "Saira-VariableFont_wdth,wght.ttf",
        "Saira.ttf",
    ]

    registered_any = False
    for filename in candidate_files:
        font_path = os.path.join(target_dir, filename)
        if os.path.isfile(font_path):
            try:
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    for fam in families:
                        if "saira" in fam.lower():
                            _saira_family = fam
                            _saira_available = True
                            registered_any = True
                            logger.info(f"Pomyślnie zarejestrowano czcionkę: {fam} z {font_path}")
                            break
                    if _saira_available:
                        break
            except Exception as e:
                logger.warning(f"Błąd rejestracji czcionki {font_path}: {e}")

    # Fallback przeskanowania wszystkich plików TTF/OTF w katalogu
    if not registered_any and os.path.isdir(target_dir):
        try:
            for entry in os.listdir(target_dir):
                if entry.lower().endswith((".ttf", ".otf")) and "saira" in entry.lower():
                    font_path = os.path.join(target_dir, entry)
                    font_id = QFontDatabase.addApplicationFont(font_path)
                    if font_id != -1:
                        _saira_available = True
                        registered_any = True
                        _saira_family = "Saira"
                        break
        except Exception as e:
            logger.warning(f"Błąd podczas przeszukiwania katalogu czcionek: {e}")

    _fonts_registered = True
    _registered_fonts_dir = target_dir
    return registered_any


def ensure_saira_font_loaded() -> bool:
    """Alias pomocniczy dla register_bundled_fonts()."""
    return register_bundled_fonts()


def is_saira_available() -> bool:
    """Zwraca True, jeśli czcionka Saira jest dostępna i zarejestrowana."""
    if not _fonts_registered:
        register_bundled_fonts()
    return _saira_available


def get_theme_font(theme_id: Optional[str]) -> str:
    """
    Zwraca główną rodzinę czcionki dla danego motywu:
    - Dla motywów EMANAGER.PRO: zwraca 'Saira' jeśli zarejestrowana, w przeciwnym razie 'Segoe UI'.
    - Dla motywów Classic / nieznanych / None: zwraca 'Segoe UI'.
    """
    if theme_id in ("emanager_dark", "emanager_light"):
        if is_saira_available():
            return _saira_family
    return FALLBACK_FONT_FAMILY


def get_theme_font_family(theme_id: Optional[str]) -> str:
    """Zwraca rodzinę czcionek w postaci ciągu CSS ze wszystkimi fallbackami."""
    return get_theme_font_css(theme_id)


def get_theme_font_css(theme_id: Optional[str]) -> str:
    """
    Zwraca bezpieczną hierarchię czcionek CSS dla reguł font-family:
    - EMANAGER.PRO: "'Saira', 'Segoe UI', sans-serif"
    - Classic / domyślne: "'Segoe UI', sans-serif"
    """
    theme = get_theme(theme_id)
    if "saira" in theme.id.lower() or "emanager" in theme.id.lower():
        return "'Saira', 'Segoe UI', sans-serif"
    return "'Segoe UI', sans-serif"


def get_theme_qfont(
    theme_id: Optional[str],
    font_size: int = 13,
    weight: QFont.Weight = QFont.Weight.Normal
) -> QFont:
    """Tworzy i zwraca obiekt QFont skonfigurowany pod dany motyw."""
    family = get_theme_font(theme_id)
    font = QFont(family, font_size)
    font.setWeight(weight)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font


# ============================================================================
# 3. GENERATOR TOKENIZOWANEGO ARKUSZA STYLÓW QSS
# ============================================================================

_THEME_QSS_CACHE: Dict[tuple, str] = {}


def clear_theme_qss_cache() -> None:
    """Czyści pamięć podręczną wygenerowanych arkuszy QSS."""
    _THEME_QSS_CACHE.clear()


def generate_theme_qss(
    theme_id: Optional[str] = DEFAULT_THEME_ID,
    font_family: Optional[str] = None,
    font_size: int = 13
) -> str:
    """
    Generuje kompletny, tokenizowany arkusz stylów QSS dla całej aplikacji.
    
    :param theme_id: Identyfikator motywu (np. 'classic_dark', 'emanager_dark')
    :param font_family: Opcjonalne nadpisanie rodziny czcionek (CSS stack)
    :param font_size: Rozmiar czcionki tekstu transkrypcji w px (np. 11-17, domyślnie 13)
    """
    t = get_theme(theme_id)
    family = font_family or get_theme_font_css(theme_id)
    if font_size is None:
        font_size = 13
    else:
        try:
            font_size = int(font_size)
        except (ValueError, TypeError):
            font_size = 13
    font_size = max(10, min(24, font_size))

    cache_key = (t.id, family, font_size)
    if cache_key in _THEME_QSS_CACHE:
        return _THEME_QSS_CACHE[cache_key]

    qss = f"""
    /* =======================================================================
       GŁÓWNE OKNO, KONTENERY I BAZA
       ======================================================================= */
    QMainWindow, QDialog, QMessageBox {{
        background-color: {t.bg_window};
        color: {t.text_primary};
    }}

    QScrollArea, 
    QScrollArea > QWidget, 
    QScrollArea > QWidget > QWidget, 
    QScrollArea QWidget#MainContainerWidget {{
        background-color: {t.bg_window};
        border: none;
    }}

    QWidget {{
        color: {t.text_primary};
        font-family: {family};
    }}

    /* =======================================================================
       PASKI PRZEWIJANIA (SCROLLBARS)
       ======================================================================= */
    QScrollBar:vertical {{
        border: none;
        background: {t.scrollbar_track};
        width: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {t.scrollbar_handle};
        min-height: 20px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t.scrollbar_handle_hover};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        border: none;
        background: none;
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
    }}

    QScrollBar:horizontal {{
        border: none;
        background: {t.scrollbar_track};
        height: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t.scrollbar_handle};
        min-width: 20px;
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {t.scrollbar_handle_hover};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        border: none;
        background: none;
        width: 0px;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
    }}

    /* =======================================================================
       PANELE, RAMKI I KARTY
       ======================================================================= */
    QGroupBox {{
        font-weight: bold;
        border: 1px solid {t.border_strong};
        border-radius: 8px;
        margin-top: 8px;
        padding-top: 14px;
        background-color: {t.bg_surface};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {t.accent};
    }}

    QFrame#DisplayFrame {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border_strong};
        border-radius: 10px;
    }}

    QFrame#ToastCard {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border_strong};
        border-radius: 8px;
    }}

    QFrame#ThemeCard {{
        background-color: {t.bg_surface};
        border: 2px solid {t.border_strong};
        border-radius: 8px;
        padding: 8px;
    }}
    QFrame#ThemeCard:hover {{
        border-color: {t.accent_hover};
    }}
    QFrame#ThemeCard[active="true"] {{
        border: 2px solid {t.accent};
        background-color: {t.bg_surface};
    }}

    /* =======================================================================
       ZAKŁADKI (QTABWIDGET & QTABBAR)
       ======================================================================= */
    QTabWidget::pane {{
        border: 1px solid {t.border_strong};
        background-color: {t.bg_surface};
        border-radius: 8px;
        padding: 12px;
    }}
    QTabBar::tab {{
        background: {t.bg_input};
        color: {t.text_secondary};
        padding: 8px 16px;
        margin-right: 4px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        font-weight: 500;
    }}
    QTabBar::tab:selected {{
        background: {t.bg_surface};
        color: {t.accent};
        font-weight: bold;
        border-bottom: 2px solid {t.accent};
    }}
    QTabBar::tab:hover {{
        color: {t.text_primary};
    }}

    /* =======================================================================
       FORMULARZE I POLA WEJŚCIOWE
       ======================================================================= */
    QLineEdit {{
        background-color: {t.bg_input};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        padding: 6px 10px;
        color: {t.text_primary};
    }}
    QLineEdit:focus {{
        border-color: {t.border_focus};
    }}
    QLineEdit:disabled {{
        background-color: {t.bg_surface};
        color: {t.text_muted};
        border-color: {t.border_subtle};
    }}

    QComboBox {{
        background-color: {t.bg_input};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        padding: 6px 10px;
        color: {t.text_primary};
    }}
    QComboBox:hover, QComboBox:focus {{
        border-color: {t.border_focus};
    }}
    QComboBox QAbstractItemView {{
        background-color: {t.bg_input};
        border: 1px solid {t.border_strong};
        selection-background-color: {t.selection_bg};
        selection-color: {t.selection_text};
        color: {t.text_primary};
    }}

    QSpinBox, QDoubleSpinBox {{
        background-color: {t.bg_input};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        padding: 4px 8px;
        color: {t.text_primary};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {t.border_focus};
    }}

    QCheckBox, QRadioButton {{
        color: {t.text_primary};
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {t.border_strong};
        border-radius: 3px;
        background-color: {t.bg_input};
    }}
    QCheckBox::indicator:hover {{
        border-color: {t.accent};
    }}
    QCheckBox::indicator:checked {{
        background-color: {t.accent};
        border-color: {t.accent};
    }}

    QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {t.border_strong};
        border-radius: 8px;
        background-color: {t.bg_input};
    }}
    QRadioButton::indicator:hover {{
        border-color: {t.accent};
    }}
    QRadioButton::indicator:checked {{
        background-color: {t.accent};
        border-color: {t.accent};
    }}

    /* =======================================================================
       PODGLĄD TRANSKRYPCJI (DYSKRETNE SKALOWANIE ROZMIARU)
       ======================================================================= */
    QTextEdit, QTextBrowser {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        padding: 8px;
        color: {t.text_primary};
        font-size: {font_size}px;
        line-height: 1.5;
    }}

    /* =======================================================================
       PRZYCISKI (BUTTONS)
       ======================================================================= */
    QPushButton {{
        background-color: {t.btn_default_bg};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        padding: 6px 14px;
        color: {t.btn_default_text};
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {t.btn_default_hover};
    }}
    QPushButton:pressed {{
        background-color: {t.accent_pressed};
        color: #ffffff;
    }}
    QPushButton:disabled {{
        background-color: {t.bg_surface};
        color: {t.text_muted};
        border-color: {t.border_subtle};
    }}

    /* Przyciski semantyczne i akcji */
    QPushButton#BtnPrimary, QPushButton#BtnSave {{
        background-color: {t.btn_primary_bg};
        border: none;
        color: {t.btn_primary_text};
        font-weight: bold;
    }}
    QPushButton#BtnPrimary:hover, QPushButton#BtnSave:hover {{
        background-color: {t.btn_primary_hover};
    }}

    QPushButton#BtnStart {{
        background-color: {t.btn_start_bg};
        border: none;
        color: {t.btn_start_text};
        font-weight: bold;
    }}
    QPushButton#BtnStart:hover {{
        background-color: {t.btn_start_hover};
    }}

    QPushButton#BtnPause {{
        background-color: {t.btn_pause_bg};
        border: none;
        color: {t.btn_pause_text};
        font-weight: bold;
    }}
    QPushButton#BtnPause:hover {{
        background-color: {t.btn_pause_hover};
    }}

    QPushButton#BtnResume {{
        background-color: {t.btn_resume_bg};
        border: none;
        color: {t.btn_resume_text};
        font-weight: bold;
    }}
    QPushButton#BtnResume:hover {{
        background-color: {t.btn_resume_hover};
    }}

    QPushButton#BtnStop {{
        background-color: {t.btn_stop_bg};
        border: none;
        color: {t.btn_stop_text};
        font-weight: bold;
    }}
    QPushButton#BtnStop:hover {{
        background-color: {t.btn_stop_hover};
    }}

    QPushButton#BtnPreset {{
        background-color: {t.btn_default_bg};
        color: {t.accent};
        font-size: 11px;
        padding: 4px 8px;
        border-radius: 4px;
    }}
    QPushButton#BtnDanger {{
        background-color: {t.btn_default_bg};
        color: #ef4444;
        font-size: 11px;
        padding: 4px 8px;
        border-radius: 4px;
        border: 1px solid #ef4444;
    }}
    QPushButton#BtnCheckUpdates {{
        background-color: {t.accent};
        color: {t.text_on_accent};
        border: none;
        border-radius: 6px;
        padding: 8px 16px;
        font-weight: bold;
        font-size: 12px;
    }}
    QPushButton#BtnCheckUpdates:hover {{
        background-color: {t.accent_hover};
    }}
    QPushButton#BtnCheckUpdates:disabled {{
        background-color: {t.bg_surface};
        color: {t.text_muted};
        border: 1px solid {t.border_subtle};
    }}

    /* Etykiety pomocnicze i opisy w oknie ustawień */
    QLabel#LblSettingDesc, QLabel#SettingDesc {{
        color: {t.text_secondary};
        font-size: 11px;
    }}
    QLabel#LblVadVal {{
        color: #10b981;
        font-weight: bold;
        min-width: 140px;
    }}
    QLabel#LblVadSysVal {{
        color: {t.speaker_system_color};
        font-weight: bold;
        min-width: 140px;
    }}

    /* Paski postępu */
    QProgressBar {{
        border: 1px solid {t.border_strong};
        border-radius: 4px;
        text-align: center;
        background-color: {t.bg_input};
        color: {t.text_primary};
        font-weight: bold;
        height: 18px;
    }}
    QProgressBar::chunk {{
        background-color: {t.btn_primary_bg};
        border-radius: 3px;
    }}

    /* =======================================================================
       LISTY PLIKÓW I HISTORII
       ======================================================================= */
    QListWidget {{
        background-color: {t.bg_input};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        color: {t.text_primary};
    }}
    QListWidget::item {{
        padding: 6px;
        border-bottom: 1px solid {t.border_subtle};
    }}
    QListWidget::item:hover {{
        background-color: {t.bg_hover};
    }}
    QListWidget::item:selected {{
        background-color: {t.selection_bg};
        color: {t.selection_text};
        font-weight: 500;
    }}

    /* =======================================================================
       SUWAKI (QSLIDER)
       ======================================================================= */
    QSlider::groove:horizontal {{
        height: 6px;
        background: {t.border_strong};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: {t.accent};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: {t.text_primary if t.is_dark else t.accent};
        width: 14px;
        margin-top: -4px;
        margin-bottom: -4px;
        border-radius: 7px;
        border: 1px solid {t.border_strong};
    }}

    /* =======================================================================
       PASKI POSTĘPU (QPROGRESSBAR)
       ======================================================================= */
    QProgressBar {{
        background-color: {t.bg_input};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        text-align: center;
        color: {t.text_primary};
        font-weight: 600;
        font-size: 11px;
    }}
    QProgressBar::chunk {{
        background-color: {t.accent};
        border-radius: 5px;
    }}
    QProgressBar#SilenceProgress::chunk {{
        background-color: #f59e0b;
        border-radius: 5px;
    }}
    QProgressBar#TranscriptionProgress::chunk {{
        background-color: {t.accent};
        border-radius: 5px;
    }}

    /* =======================================================================
       ETYKIETY STATUSU NAGRYWANIA
       ======================================================================= */
    QLabel#StatusStopped {{
        background-color: {t.status_stopped_bg};
        color: {t.status_stopped_text};
        padding: 4px 14px;
        border-radius: 12px;
        font-weight: bold;
        font-size: 11px;
    }}
    QLabel#StatusSpeech {{
        background-color: {t.status_speech_bg};
        color: {t.status_speech_text};
        padding: 4px 14px;
        border-radius: 12px;
        font-weight: bold;
        font-size: 11px;
    }}
    QLabel#StatusCountdown {{
        background-color: {t.status_countdown_bg};
        color: {t.status_countdown_text};
        padding: 4px 14px;
        border-radius: 12px;
        font-weight: bold;
        font-size: 11px;
    }}
    QLabel#StatusAutoPaused {{
        background-color: {t.status_autopaused_bg};
        color: {t.status_autopaused_text};
        padding: 4px 14px;
        border-radius: 12px;
        font-weight: bold;
        font-size: 11px;
    }}
    QLabel#StatusManualPaused {{
        background-color: {t.status_manualpaused_bg};
        color: {t.status_manualpaused_text};
        padding: 4px 14px;
        border-radius: 12px;
        font-weight: bold;
        font-size: 11px;
    }}

    /* =======================================================================
       TOOLTIPS, KOMUNIKATY I MENU
       ======================================================================= */
    QToolTip {{
        background-color: {t.bg_elevated};
        color: {t.text_primary};
        border: 1px solid {t.border_strong};
        padding: 4px 8px;
        border-radius: 4px;
    }}

    QMessageBox {{
        background-color: {t.bg_window};
        color: {t.text_primary};
    }}
    QMessageBox QLabel {{
        background-color: transparent;
        color: {t.text_primary};
        font-size: 12px;
    }}
    QMessageBox QPushButton {{
        background-color: {t.btn_default_bg};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        padding: 6px 16px;
        color: {t.text_primary};
        min-width: 65px;
    }}
    QMessageBox QPushButton:hover {{
        background-color: {t.btn_default_hover};
    }}

    QMenu {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border_strong};
        color: {t.text_primary};
        padding: 4px;
        border-radius: 6px;
    }}
    QMenu::item {{
        padding: 6px 20px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {t.selection_bg};
        color: {t.selection_text};
    }}
    QMenu::separator {{
        height: 1px;
        background-color: {t.border_strong};
        margin: 4px 6px;
    }}

    /* =======================================================================
       OKNO GŁÓWNE I ELEMENTY POWIADOMIEŃ (SILENCETOASTBANNER)
       ======================================================================= */
    QFrame#ToastCard, #ToastCard {{
        background-color: {t.bg_surface};
        border: 2px solid #f59e0b;
        border-radius: 12px;
    }}
    QLabel#ToastAppName {{
        color: {t.text_secondary};
        font-size: 10px;
        font-weight: bold;
    }}
    QPushButton#ToastCloseBtn {{
        background: transparent;
        color: {t.text_muted};
        border: none;
        padding: 0px;
        margin: 0px;
        font-size: 13px;
        font-weight: bold;
        border-radius: 11px;
    }}
    QPushButton#ToastCloseBtn:hover {{
        color: #ffffff;
        background-color: #ef4444;
    }}
    QLabel#ToastTitle {{
        color: #f59e0b;
        font-size: 13px;
        font-weight: bold;
    }}
    QLabel#ToastDesc {{
        color: {t.text_secondary};
        font-size: 11px;
    }}
    QLabel#ToastTimer {{
        color: {t.text_muted};
        font-size: 10px;
    }}
    QPushButton#ToastOkBtn {{
        background-color: {t.btn_default_bg};
        color: {t.btn_default_text};
        border: 1px solid {t.border_strong};
        border-radius: 4px;
        padding: 3px 10px;
        font-size: 10px;
    }}
    QPushButton#ToastOkBtn:hover {{
        background-color: {t.btn_default_hover};
        color: {t.text_primary};
    }}
    QPushButton#ToastActionBtn {{
        background-color: {t.accent};
        color: {t.text_on_accent};
        border: none;
        border-radius: 4px;
        padding: 3px 10px;
        font-size: 10px;
        font-weight: bold;
    }}
    QPushButton#ToastActionBtn:hover {{
        background-color: {t.accent_hover};
    }}

    /* =======================================================================
       SEKCJA NAGŁÓWKA I BANERA AKTUALIZACJI
       ======================================================================= */
    QLabel#HeaderSubtitle {{
        color: {t.accent};
        font-size: 12px;
        font-weight: bold;
    }}
    QPushButton#BtnSettings {{
        background-color: {t.bg_surface};
        color: {t.accent};
        border: 1px solid {t.border_strong};
        border-radius: 8px;
        padding: 10px 18px;
        font-weight: bold;
        font-size: 12px;
    }}
    QPushButton#BtnSettings:hover {{
        background-color: {t.bg_hover};
        color: {t.text_primary};
        border-color: {t.accent};
    }}
    QFrame#UpdateBanner {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.accent};
        border-radius: 8px;
    }}
    QLabel#UpdateBannerText {{
        color: {t.accent};
        font-size: 12px;
        font-weight: bold;
        border: none;
        background: transparent;
    }}
    QPushButton#UpdateBannerActionBtn {{
        background-color: {t.accent};
        color: {t.text_on_accent};
        border: none;
        border-radius: 6px;
        padding: 6px 14px;
        font-weight: bold;
        font-size: 11px;
    }}
    QPushButton#UpdateBannerActionBtn:hover {{
        background-color: {t.accent_hover};
    }}
    QPushButton#BannerCloseBtn {{
        background: transparent;
        color: {t.text_secondary};
        border: none;
        font-size: 13px;
        font-weight: bold;
        padding: 4px 8px;
    }}
    QPushButton#BannerCloseBtn:hover {{
        color: {t.text_primary};
    }}

    /* =======================================================================
       ŹRÓDŁA DŹWIĘKU I ETYKIETY TRYBU
       ======================================================================= */
    QLabel#AudioSourceModeLabel {{
        color: {t.accent};
        font-size: 11px;
        font-weight: bold;
    }}
    QLabel#LblMicInput {{
        color: {t.text_secondary};
        font-size: 11px;
    }}
    QLabel#LblMicInput[active="true"] {{
        color: {t.speaker_mic_color};
        font-weight: bold;
    }}
    QLabel#LblSysInput, QLabel#LblAppInput {{
        color: {t.text_secondary};
        font-size: 11px;
    }}
    QLabel#LblSysInput[active="true"], QLabel#LblAppInput[active="true"] {{
        color: {t.speaker_system_color};
        font-weight: bold;
    }}

    /* =======================================================================
       INFORMACJE O MODELU AI I SPRZĘCIE
       ======================================================================= */
    QLabel#ModelDescLabel {{
        color: {t.text_secondary};
        font-size: 11px;
        margin-top: 2px;
    }}
    QLabel#HwBadgeLabel {{
        color: #10b981;
        font-weight: bold;
        font-size: 11px;
        margin-top: 4px;
    }}

    /* =======================================================================
       TIMER I PROGI CISZY
       ======================================================================= */
    QLabel#TimerLabel {{
        color: {t.text_primary};
        margin: 4px 0;
    }}
    QLabel#SilenceValLabel {{
        color: #f59e0b;
    }}
    QLabel#ThreshValLabel {{
        color: {t.accent};
    }}

    /* =======================================================================
       VU METER I PRZYCISKI WYCISZANIA (MUTE)
       ======================================================================= */
    QLabel#VuMicTitle {{
        color: {t.speaker_mic_color};
        font-size: 11px;
        font-weight: bold;
    }}
    QProgressBar#VuMicProgress {{
        border: 1px solid {t.border_strong};
        border-radius: 4px;
        background-color: {t.bg_input};
    }}
    QProgressBar#VuMicProgress::chunk {{
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.speaker_mic_color}, stop:0.8 #4895ef, stop:1 #f72585);
        border-radius: 3px;
    }}
    QLabel#VuSysTitle {{
        color: {t.speaker_system_color};
        font-size: 11px;
        font-weight: bold;
    }}
    QProgressBar#VuSysProgress {{
        border: 1px solid {t.border_strong};
        border-radius: 4px;
        background-color: {t.bg_input};
    }}
    QProgressBar#VuSysProgress::chunk {{
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.speaker_system_color}, stop:0.8 #7209b7, stop:1 #f72585);
        border-radius: 3px;
    }}

    QPushButton#BtnMuteMic, QPushButton#BtnMuteSys {{
        background-color: {t.btn_default_bg};
        color: {t.btn_default_text};
        border: 1px solid {t.border_strong};
        border-radius: 4px;
        font-size: 11px;
    }}
    QPushButton#BtnMuteMic:hover {{
        background-color: {t.btn_default_hover};
        border-color: {t.speaker_mic_color};
    }}
    QPushButton#BtnMuteSys:hover {{
        background-color: {t.btn_default_hover};
        border-color: {t.speaker_system_color};
    }}
    QPushButton#BtnMuteMic[muted="true"], QPushButton#BtnMuteSys[muted="true"] {{
        background-color: #ef4444;
        color: #ffffff;
        border: 1px solid #dc2626;
        font-weight: bold;
    }}
    QPushButton#BtnMuteMic[muted="true"]:hover, QPushButton#BtnMuteSys[muted="true"]:hover {{
        background-color: #dc2626;
    }}

    /* =======================================================================
       ETYKIETA SZCZEGÓŁÓW DETEKCJI MOWY (VAD)
       ======================================================================= */
    QLabel#VadDetail {{
        color: {t.text_secondary};
        font-size: 11px;
        margin-top: 4px;
    }}
    QLabel#VadDetail[vad_state="speech"] {{
        color: #10b981;
        font-weight: bold;
    }}
    QLabel#VadDetail[vad_state="paused"] {{
        color: #f59e0b;
        font-weight: bold;
    }}
    QLabel#VadDetail[vad_state="silence"] {{
        color: {t.text_secondary};
        font-weight: normal;
    }}

    /* =======================================================================
       SEKCJA DIARYZACJI, LIST I PODGLĄDU
       ======================================================================= */
    QPushButton#BtnUploadAudio {{
        background-color: #3a0ca3;
        color: #ffffff;
        border-radius: 8px;
        font-weight: bold;
        font-size: 11px;
        padding: 0 14px;
    }}
    QPushButton#BtnUploadAudio:hover {{
        background-color: #4361ee;
    }}
    QLabel#SpeakerCountLabel, QLabel#AudioPathLabel, QLabel#TxtPathLabel {{
        color: {t.text_secondary};
        font-size: 11px;
    }}
    QPushButton#BtnRunDiarization {{
        background-color: #7209b7;
        color: #ffffff;
        font-weight: bold;
        border-radius: 4px;
        padding: 0 10px;
        font-size: 10px;
    }}
    QPushButton#BtnRunDiarization:hover {{
        background-color: #5a0792;
    }}
    QGroupBox#SpeakerBox {{
        border: 1px solid {t.accent};
        border-radius: 6px;
        margin-top: 10px;
        font-weight: bold;
    }}
    QLabel#SpeakerInfoLabel {{
        color: {t.accent};
        font-size: 11px;
    }}
    QScrollArea#SpeakerScrollArea, QWidget#SpeakerScrollWidget {{
        background: transparent;
    }}
    QPushButton#BtnApplySpeakers {{
        background-color: #2b9348;
        color: #ffffff;
        font-weight: bold;
        border-radius: 6px;
        font-size: 11px;
    }}
    QPushButton#BtnApplySpeakers:hover {{
        background-color: #23783a;
    }}
    QPushButton#BtnCopyTranscript {{
        background-color: {t.bg_surface};
        color: {t.accent};
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        font-weight: bold;
        font-size: 11px;
        padding: 0 14px;
    }}
    QPushButton#BtnCopyTranscript:hover {{
        background-color: {t.bg_hover};
        color: {t.text_primary};
    }}
    QPushButton#BtnSaveTranscript {{
        background-color: {t.bg_surface};
        color: #10b981;
        border: 1px solid {t.border_strong};
        border-radius: 6px;
        font-weight: bold;
        font-size: 11px;
        padding: 0 14px;
    }}
    QPushButton#BtnSaveTranscript:hover {{
        background-color: {t.bg_hover};
        color: {t.text_primary};
    }}

    /* =======================================================================
       MAPOWANIE MÓWCÓW (SPEAKER MAPPING CARDS)
       ======================================================================= */
    QFrame#SpeakerCard {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border_strong};
        border-radius: 8px;
        padding: 6px;
    }}
    QLabel#SpeakerIdLabel {{
        color: {t.text_primary};
        font-size: 12px;
        font-weight: bold;
    }}
    QLabel#SpeakerStatsLabel {{
        color: #10b981;
        font-size: 11px;
        font-weight: bold;
    }}
    QLineEdit#SpeakerNameEdit {{
        background-color: {t.bg_input};
        color: {t.text_primary};
        border: 1px solid {t.accent};
        border-radius: 4px;
        padding: 5px 8px;
        font-weight: bold;
        font-size: 12px;
    }}
    QLineEdit#SpeakerRoleEdit {{
        background-color: {t.bg_input};
        color: #f59e0b;
        border: 1px solid #f59e0b;
        border-radius: 4px;
        padding: 5px 8px;
        font-size: 11px;
    }}
    QLabel#SpeakerClueLabel {{
        color: {t.accent};
        font-size: 11px;
    }}
    QLabel#SpeakerSampleLabel {{
        color: {t.text_secondary};
        font-size: 11px;
    }}

    /* =======================================================================
       PASEK INTEGRACJI CHMUROWEJ
       ======================================================================= */
    QLabel#CloudStatus {{
        font-size: 11px;
        font-weight: bold;
        color: {t.accent};
    }}
    QLabel#CloudStatus[status="info"] {{
        color: {t.accent};
    }}
    QLabel#CloudStatus[status="success"] {{
        color: #10b981;
    }}
    QLabel#CloudStatus[status="warning"] {{
        color: #f59e0b;
    }}
    QLabel#CloudStatus[status="error"] {{
        color: #ef4444;
    }}
    QLabel#CloudStatus[status="purple"] {{
        color: {t.speaker_system_color};
    }}

    QPushButton#BtnManualSync {{
        background-color: {t.accent};
        color: {t.text_on_accent};
        font-weight: bold;
        border-radius: 4px;
        font-size: 10px;
        padding: 0 12px;
    }}
    QPushButton#BtnManualSync:hover {{
        background-color: {t.accent_hover};
    }}
    """
    _THEME_QSS_CACHE[cache_key] = qss
    return qss


# ============================================================================
# 4. PALETA QPALETTE (STYL FUSION)
# ============================================================================

def setup_theme_palette(app: Optional[QApplication] = None, theme_id: str = DEFAULT_THEME_ID) -> None:
    """
    Konfiguruje spójną paletę QPalette dla całej aplikacji Qt.
    Wymusza styl Fusion, gwarantując niezależność od motywu Windows.
    """
    if app is None:
        app = QApplication.instance()
    if app is None:
        return

    theme = get_theme(theme_id)

    try:
        cur_style = app.style()
        if cur_style is None or cur_style.objectName().lower() != "fusion":
            app.setStyle("Fusion")
    except Exception:
        pass

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.bg_window))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.bg_surface if theme.is_dark else theme.bg_input))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.bg_input if theme.is_dark else theme.bg_surface))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(theme.bg_elevated))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme.btn_default_bg))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.btn_default_text))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(theme.text_on_accent))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.accent))
    palette.setColor(
        QPalette.ColorRole.HighlightedText,
        QColor(theme.text_on_accent if not theme.is_dark else "#111216" if theme.id == "classic_dark" else "#ffffff")
    )

    # Elementy wygaszone (Disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(theme.text_muted))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(theme.text_muted))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(theme.text_muted))

    app.setPalette(palette)


# ============================================================================
# 5. MAPPING KOLORÓW MÓWCÓW W TRANSKRYPCJI (O(1))
# ============================================================================

# Re-eksportowane z recorder.config dla pelnej kompatybilnosci wstecznej i braku importow kolowych:
from recorder.config import THEME_SPEAKER_COLORS, get_speaker_colors


def get_speaker_color_for_channel(theme_id: Optional[str], channel: str) -> str:
    """Zwraca kolor mówcy dla danego kanału ('mic' lub 'system')."""
    colors = get_speaker_colors(theme_id)
    return colors.get(channel, colors["mic"])


# ============================================================================
# 6. INTEGRACJA Z WINDOWS DWM TITLEBAR
# ============================================================================

def set_window_titlebar_theme(hwnd: Union[int, object], is_dark: bool) -> bool:
    """
    Dostosowuje natywny pasek tytułu okna Windows 10/11 do trybu ciemnego lub jasnego.
    Używa DwmSetWindowAttribute (atrybut 20 z fallbackiem na 19).
    Bezpieczny no-op na innych platformach oraz przy braku uprawnień.
    """
    if sys.platform != "win32":
        return False

    if hwnd is None:
        return False

    try:
        if hasattr(hwnd, "winId"):
            hwnd_val = int(hwnd.winId())
        elif isinstance(hwnd, int):
            hwnd_val = hwnd
        else:
            return False
    except Exception:
        return False

    if hwnd_val <= 0:
        return False

    try:
        import ctypes

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19

        val = ctypes.c_int(1 if is_dark else 0)
        dwmapi = ctypes.windll.dwmapi

        # 1. Główna próba z atrybutem 20 (Windows 11 oraz Windows 10 20H1+)
        res = dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd_val),
            ctypes.c_uint(DWMWA_USE_IMMERSIVE_DARK_MODE),
            ctypes.byref(val),
            ctypes.sizeof(val)
        )
        if res == 0:
            _trigger_titlebar_redraw(hwnd_val)
            return True

        # 2. Próba awaryjna z atrybutem 19 (Windows 10 1809/1903/1909)
        res_fallback = dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd_val),
            ctypes.c_uint(DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1),
            ctypes.byref(val),
            ctypes.sizeof(val)
        )
        if res_fallback == 0:
            _trigger_titlebar_redraw(hwnd_val)
            return True

        return False
    except Exception as e:
        logger.debug(f"DWM title bar adaptation failed or not supported: {e}")
        return False


def _trigger_titlebar_redraw(hwnd: int) -> None:
    """Wymusza natychmiastowe odświeżenie obszaru non-client okna (paska tytułu)."""
    try:
        import ctypes
        SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0020  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            None,
            0, 0, 0, 0,
            SWP_FLAGS
        )
    except Exception:
        pass


def is_theme_dark(theme_id: Optional[str]) -> bool:
    """Zwraca True, jeśli motyw jest ciemny."""
    theme = get_theme(theme_id)
    return theme.is_dark


# ============================================================================
# 7. GŁÓWNA FUNKCJA APLIKUJĄCA MOTYW (apply_theme)
# ============================================================================

def apply_theme(
    app: Optional[QApplication] = None,
    theme_id: Optional[str] = DEFAULT_THEME_ID,
    font_size: int = 13,
    window: Optional[object] = None,
    hwnd: Optional[Union[int, object]] = None
) -> str:
    """
    Koordynuje pełną aplikację motywu:
    1. Waliduje identyfikator motywu (bezpieczny fallback do classic_dark).
    2. Rejestruje dołączoną czcionkę Saira (jeśli dostępna).
    3. Konfiguruje paletę QPalette (Fusion style).
    4. Generuje i ustawia arkusz stylów QSS na aplikacji i opcjonalnym oknie.
    5. Dostosowuje natywny pasek tytułu Windows DWM (ciemny/jasny).
    Zwraca wygenerowany string QSS.
    """
    if app is None:
        app = QApplication.instance()

    theme = get_theme(theme_id)
    register_bundled_fonts()

    family_css = get_theme_font_css(theme.id)
    qss = generate_theme_qss(theme.id, font_family=family_css, font_size=font_size)

    if app is not None:
        setup_theme_palette(app, theme.id)
        app.setStyleSheet(qss)

    target_win = window if window is not None else hwnd
    if target_win is not None:
        if sys.platform == "win32":
            try:
                set_window_titlebar_theme(target_win, theme.is_dark)
            except Exception:
                pass
    elif app is not None and sys.platform == "win32":
        try:
            for w in app.topLevelWidgets():
                if w.isWindow():
                    set_window_titlebar_theme(w, theme.is_dark)
        except Exception:
            pass

    return qss


# ============================================================================
# 8. WSTECZNA KOMPATYBILNOŚĆ (LEGACY ALIASES)
# ============================================================================

DARK_THEME_QSS = generate_theme_qss("classic_dark")


def setup_dark_palette(app: Optional[QApplication] = None) -> None:
    """Alias wstecznej kompatybilności dla setup_theme_palette('classic_dark')."""
    setup_theme_palette(app, "classic_dark")
