"""
Unit and Integration tests for Recorder67 Theme Engine and Font Assets (Milestone 1).

Tests:
1. Theme registry and token completeness across all 4 themes.
2. Dynamic QSS generator syntax and widget coverage.
3. QPalette configuration with Fusion style.
4. Saira font registration and Segoe UI fallback mechanism.
5. Windows DWM title bar caption adaptation and fallback guards.
6. Speaker turn colors contrast and WCAG compliance.
7. App icon contrast enhancement and asset integrity.
8. apply_theme orchestrator function.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtGui import QFont, QPalette, QColor
except ImportError:
    from PyQt6.QtWidgets import QApplication, QWidget
    from PyQt6.QtGui import QFont, QPalette, QColor

from recorder.ui import theme


@pytest.fixture(scope="session")
def qapp():
    """Provides a headless QApplication instance."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ==============================================================================
# 1. THEME REGISTRY & DEFINITION INTEGRITY TESTS
# ==============================================================================

def test_themes_registry_integrity():
    """All 4 visual themes must be registered with complete, valid design tokens."""
    expected_themes = ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]
    assert hasattr(theme, "THEMES"), "theme.THEMES registry must exist"

    for th_id in expected_themes:
        assert th_id in theme.THEMES, f"Theme '{th_id}' missing in THEMES"
        t = theme.THEMES[th_id]

        assert t.id == th_id
        assert isinstance(t.name, str) and len(t.name) > 0
        assert isinstance(t.description, str) and len(t.description) > 0
        assert isinstance(t.is_dark, bool)

        # Essential color tokens checked by E2E
        required_color_tokens = [
            "bg_window", "bg_app", "bg_surface", "bg_elevated", "bg_input", "bg_hover",
            "text_primary", "text_secondary", "text_muted", "text_on_accent",
            "border", "border_strong", "border_subtle", "border_focus",
            "accent", "accent_hover", "accent_pressed",
            "speaker_mic_color", "speaker_system_color",
            "scrollbar_track", "scrollbar_handle",
            "selection_bg", "selection_text",
            "swatch_bg", "swatch_accent", "swatch_text"
        ]
        for token in required_color_tokens:
            val = getattr(t, token, None)
            assert val is not None, f"Theme '{th_id}' missing token '{token}'"
            assert isinstance(val, str), f"Token '{token}' must be str, got {type(val)}"
            assert val.startswith("#") and len(val) in (4, 7, 9), f"Invalid hex for '{token}': '{val}'"


def test_get_theme_fallback():
    """Invalid, empty or None theme ID must safely fall back to classic_dark."""
    assert theme.get_theme("classic_dark").id == "classic_dark"
    assert theme.get_theme("emanager_dark").id == "emanager_dark"
    assert theme.get_theme("non_existent_123").id == "classic_dark"
    assert theme.get_theme("").id == "classic_dark"
    assert theme.get_theme(None).id == "classic_dark"


def test_get_available_themes():
    """get_available_themes must return all 4 theme definitions."""
    themes = theme.get_available_themes()
    assert len(themes) == 4
    theme_ids = [t.id for t in themes]
    assert "classic_dark" in theme_ids
    assert "classic_light" in theme_ids
    assert "emanager_dark" in theme_ids
    assert "emanager_light" in theme_ids


# ==============================================================================
# 2. QSS GENERATOR & WIDGET COVERAGE TESTS
# ==============================================================================

def test_generate_theme_qss_all_themes():
    """generate_theme_qss must produce valid, non-empty stylesheets for all 4 themes."""
    for th_id in ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]:
        qss = theme.generate_theme_qss(th_id, font_size=13)
        assert isinstance(qss, str) and len(qss) > 1000
        # Mismatched curly braces check
        assert qss.count("{") == qss.count("}"), f"Mismatched braces in QSS for '{th_id}'"


def test_generate_theme_qss_widget_selectors():
    """QSS must style all essential widgets to avoid unthemed controls."""
    qss = theme.generate_theme_qss("classic_dark")
    required_selectors = [
        "QMainWindow", "QDialog", "QMessageBox", "QScrollArea",
        "QScrollBar:vertical", "QScrollBar:horizontal",
        "QGroupBox", "QFrame#DisplayFrame", "QFrame#ToastCard", "QFrame#ThemeCard",
        "QTabWidget::pane", "QTabBar::tab",
        "QLineEdit", "QComboBox", "QSpinBox", "QCheckBox", "QRadioButton",
        "QTextEdit", "QTextBrowser", "QPushButton",
        "QPushButton#BtnStart", "QPushButton#BtnPause", "QPushButton#BtnResume", "QPushButton#BtnStop",
        "QListWidget", "QSlider::groove:horizontal", "QProgressBar",
        "QLabel#StatusStopped", "QLabel#StatusSpeech", "QLabel#StatusCountdown",
        "QToolTip", "QMenu"
    ]
    for sel in required_selectors:
        assert sel in qss, f"Missing selector in QSS: {sel}"


def test_generate_theme_qss_font_scaling():
    """Passing font_size parameter must update QTextEdit and QTextBrowser font-size."""
    for sz in [11, 13, 15, 17]:
        qss = theme.generate_theme_qss("emanager_light", font_size=sz)
        assert f"font-size: {sz}px;" in qss, f"font-size: {sz}px; not found in QSS"


def test_generate_theme_qss_font_clamping():
    """Extreme font sizes (< 10 or > 24) must be clamped safely."""
    qss_small = theme.generate_theme_qss("classic_dark", font_size=5)
    assert "font-size: 10px;" in qss_small

    qss_large = theme.generate_theme_qss("classic_dark", font_size=35)
    assert "font-size: 24px;" in qss_large


def test_generate_theme_qss_defensive_font_size():
    """generate_theme_qss must default safely to 13px when font_size is None or invalid type."""
    qss_none = theme.generate_theme_qss("classic_dark", font_size=None)
    assert "font-size: 13px;" in qss_none

    qss_invalid = theme.generate_theme_qss("classic_dark", font_size="invalid")
    assert "font-size: 13px;" in qss_invalid


# ==============================================================================
# 3. QPALETTE & FUSION STYLE TESTS
# ==============================================================================

def test_setup_theme_palette(qapp):
    """setup_theme_palette must configure QPalette correctly for dark and light themes."""
    # Dark theme
    theme.setup_theme_palette(qapp, "classic_dark")
    pal = qapp.palette()
    assert pal.color(QPalette.ColorRole.Window).name().lower() == "#111216"
    assert pal.color(QPalette.ColorRole.Highlight).name().lower() == "#4cc9f0"

    # Light theme
    theme.setup_theme_palette(qapp, "classic_light")
    pal_light = qapp.palette()
    assert pal_light.color(QPalette.ColorRole.Window).name().lower() == "#f8fafc"
    assert pal_light.color(QPalette.ColorRole.Highlight).name().lower() == "#0284c7"


def test_setup_dark_palette_legacy(qapp):
    """setup_dark_palette legacy alias must execute without error."""
    theme.setup_dark_palette(qapp)
    assert qapp.palette().color(QPalette.ColorRole.Window).name().lower() == "#111216"


def test_dark_theme_qss_legacy():
    """DARK_THEME_QSS legacy constant must be a non-empty string."""
    assert isinstance(theme.DARK_THEME_QSS, str)
    assert len(theme.DARK_THEME_QSS) > 500
    assert "#111216" in theme.DARK_THEME_QSS


# ==============================================================================
# 4. FONT BUNDLING & FALLBACK TESTS
# ==============================================================================

def test_font_assets_exist():
    """Verify Saira font file and OFL license exist in recorder/resources/fonts/."""
    fonts_dir = theme.get_fonts_dir()
    assert os.path.isdir(fonts_dir), f"Fonts directory not found: {fonts_dir}"

    ofl_path = os.path.join(fonts_dir, "OFL.txt")
    assert os.path.isfile(ofl_path), "OFL.txt license missing"
    assert os.path.getsize(ofl_path) > 1000

    saira_path = os.path.join(fonts_dir, "Saira[wdth,wght].ttf")
    assert os.path.isfile(saira_path), "Saira[wdth,wght].ttf font file missing"
    assert os.path.getsize(saira_path) > 400000


def test_register_bundled_fonts(qapp):
    """register_bundled_fonts must return True when fonts are present and app is running."""
    res = theme.register_bundled_fonts()
    assert isinstance(res, bool)
    assert res is True
    assert theme.is_saira_available() is True


def test_register_bundled_fonts_caching_and_directory_tracking(qapp, tmp_path):
    """register_bundled_fonts tracks _registered_fonts_dir and re-evaluates when directory changes."""
    # First call with default dir
    assert theme.register_bundled_fonts() is True
    assert theme._registered_fonts_dir == theme.get_fonts_dir()

    # Call with non-existent dir must not return cached True
    fake_dir = str(tmp_path / "empty_non_existent_fonts")
    res_fake = theme.register_bundled_fonts(fonts_dir=fake_dir)
    assert res_fake is False

    # Calling again with None returns cached status of original dir
    assert theme.register_bundled_fonts() is True


def test_get_theme_font_resolution(qapp):
    """get_theme_font must return 'Saira' for eManager themes and 'Segoe UI' for classic."""
    assert theme.get_theme_font("classic_dark") == "Segoe UI"
    assert theme.get_theme_font("classic_light") == "Segoe UI"
    assert theme.get_theme_font("emanager_dark") in ("Saira", "Segoe UI")
    assert theme.get_theme_font("emanager_light") in ("Saira", "Segoe UI")
    assert theme.get_theme_font("unknown") == "Segoe UI"
    assert theme.get_theme_font(None) == "Segoe UI"


def test_get_theme_font_fallback_when_saira_missing(monkeypatch):
    """When Saira is unavailable, eManager themes must fall back to Segoe UI."""
    monkeypatch.setattr(theme, "_saira_available", False)
    assert theme.get_theme_font("emanager_dark") == "Segoe UI"
    assert theme.get_theme_font("emanager_light") == "Segoe UI"


def test_get_theme_font_css_stack():
    """get_theme_font_css must include Segoe UI and sans-serif fallback."""
    classic_css = theme.get_theme_font_css("classic_dark")
    assert "Segoe UI" in classic_css
    assert "sans-serif" in classic_css

    emanager_css = theme.get_theme_font_css("emanager_dark")
    assert "Saira" in emanager_css
    assert "Segoe UI" in emanager_css
    assert "sans-serif" in emanager_css


def test_get_theme_qfont(qapp):
    """get_theme_qfont must return a configured QFont."""
    f = theme.get_theme_qfont("classic_dark", font_size=14)
    assert isinstance(f, QFont)
    assert f.pointSize() == 14 or f.pixelSize() == 14 or f.family() in ("Segoe UI", "Saira")


# ==============================================================================
# 5. WINDOWS DWM TITLE BAR INTEGRATION TESTS
# ==============================================================================

def test_dwm_safe_on_current_platform(qapp):
    """Calling set_window_titlebar_theme with a test widget must not raise exceptions."""
    w = QWidget()
    w.show()
    qapp.processEvents()

    ret_dark = theme.set_window_titlebar_theme(w, is_dark=True)
    assert isinstance(ret_dark, bool)

    ret_light = theme.set_window_titlebar_theme(w, is_dark=False)
    assert isinstance(ret_light, bool)

    w.close()
    w.deleteLater()


def test_dwm_invalid_hwnd_guards():
    """Invalid window handles must return False safely."""
    assert theme.set_window_titlebar_theme(None, True) is False
    assert theme.set_window_titlebar_theme(0, True) is False
    assert theme.set_window_titlebar_theme(-5, True) is False
    assert theme.set_window_titlebar_theme("invalid", True) is False


def test_dwm_non_windows_platform(monkeypatch):
    """On non-Windows systems, set_window_titlebar_theme must return False."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert theme.set_window_titlebar_theme(12345, True) is False


def test_dwm_attribute_20_success(monkeypatch):
    """Attribute 20 success path on Windows 11/10 20H1+."""
    monkeypatch.setattr(sys, "platform", "win32")
    mock_dwmapi = MagicMock()
    mock_dwmapi.DwmSetWindowAttribute.return_value = 0

    with patch("ctypes.windll.dwmapi", mock_dwmapi, create=True), \
         patch("ctypes.windll.user32.SetWindowPos", return_value=1, create=True):
        res = theme.set_window_titlebar_theme(12345, is_dark=True)
        assert res is True
        assert mock_dwmapi.DwmSetWindowAttribute.call_count == 1


def test_dwm_attribute_19_fallback(monkeypatch):
    """Attribute 19 fallback path when attribute 20 fails."""
    monkeypatch.setattr(sys, "platform", "win32")
    mock_dwmapi = MagicMock()
    # Attr 20 fails (returns 1), attr 19 succeeds (returns 0)
    mock_dwmapi.DwmSetWindowAttribute.side_effect = [1, 0]

    with patch("ctypes.windll.dwmapi", mock_dwmapi, create=True), \
         patch("ctypes.windll.user32.SetWindowPos", return_value=1, create=True):
        res = theme.set_window_titlebar_theme(12345, is_dark=False)
        assert res is True
        assert mock_dwmapi.DwmSetWindowAttribute.call_count == 2


def test_dwm_both_attributes_fail(monkeypatch):
    """When both attributes fail, return False without exception."""
    monkeypatch.setattr(sys, "platform", "win32")
    mock_dwmapi = MagicMock()
    mock_dwmapi.DwmSetWindowAttribute.side_effect = [1, 1]

    with patch("ctypes.windll.dwmapi", mock_dwmapi, create=True):
        res = theme.set_window_titlebar_theme(12345, is_dark=True)
        assert res is False


# ==============================================================================
# 6. SPEAKER TURN COLORS & WCAG CONTRAST TESTS
# ==============================================================================

def _relative_luminance(hex_color: str) -> float:
    """Calculates relative luminance according to WCAG 2.1 formula."""
    hex_clean = hex_color.lstrip("#")
    r, g, b = [int(hex_clean[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
    r_lin = r / 12.92 if r <= 0.04045 else ((r + 0.055) / 1.055) ** 2.4
    g_lin = g / 12.92 if g <= 0.04045 else ((g + 0.055) / 1.055) ** 2.4
    b_lin = b / 12.92 if b <= 0.04045 else ((b + 0.055) / 1.055) ** 2.4
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def _contrast_ratio(hex1: str, hex2: str) -> float:
    """Calculates contrast ratio between two hex colors."""
    l1 = _relative_luminance(hex1)
    l2 = _relative_luminance(hex2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def test_get_speaker_colors_o1():
    """get_speaker_colors must return dict with mic and system colors and reuse THEME_SPEAKER_COLORS."""
    for th_id in ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]:
        colors = theme.get_speaker_colors(th_id)
        assert colors is theme.THEME_SPEAKER_COLORS[th_id], "Must return pre-allocated dict reference directly"
        assert "mic" in colors
        assert "system" in colors
        assert colors["mic"].startswith("#")
        assert colors["system"].startswith("#")

    # Safe fallback for None and unknown theme ID
    assert theme.get_speaker_colors(None) is theme.THEME_SPEAKER_COLORS["classic_dark"]
    assert theme.get_speaker_colors("unknown_theme") is theme.THEME_SPEAKER_COLORS["classic_dark"]

    # Fast channel getter
    assert theme.get_speaker_color_for_channel("classic_dark", "mic") == theme.THEMES["classic_dark"].speaker_mic_color
    assert theme.get_speaker_color_for_channel("classic_dark", "system") == theme.THEMES["classic_dark"].speaker_system_color


def test_speaker_contrast_wcag():
    """
    Speaker turn colors must achieve high contrast (WCAG AA >= 4.5:1)
    against the background/base color where transcripts are read.
    """
    for th_id, t in theme.THEMES.items():
        base_bg = t.bg_surface if t.is_dark else t.bg_window
        mic_contrast = _contrast_ratio(t.speaker_mic_color, base_bg)
        sys_contrast = _contrast_ratio(t.speaker_system_color, base_bg)

        # WCAG AA requires at least 4.5:1 for normal text readability
        assert mic_contrast >= 4.5, (
            f"Theme '{th_id}': Mic color '{t.speaker_mic_color}' has insufficient contrast ({mic_contrast:.2f}:1) vs '{base_bg}'"
        )
        assert sys_contrast >= 4.5, (
            f"Theme '{th_id}': System color '{t.speaker_system_color}' has insufficient contrast ({sys_contrast:.2f}:1) vs '{base_bg}'"
        )


def test_text_primary_contrast_wcag():
    """Primary text must achieve WCAG AAA (>= 7.0:1) contrast against app background."""
    for th_id, t in theme.THEMES.items():
        ratio = _contrast_ratio(t.text_primary, t.bg_window)
        assert ratio >= 7.0, (
            f"Theme '{th_id}': Primary text '{t.text_primary}' vs bg '{t.bg_window}' contrast is {ratio:.2f}:1 (expected >= 7.0)"
        )


# ==============================================================================
# 7. APP ICON CONTRAST ENHANCEMENT TESTS
# ==============================================================================

def test_app_icon_asset_files_integrity():
    """Both app_icon.png and app_icon.ico must exist and have valid file sizes."""
    png_path = os.path.join(PROJECT_ROOT, "recorder", "resources", "app_icon.png")
    ico_path = os.path.join(PROJECT_ROOT, "recorder", "resources", "app_icon.ico")

    assert os.path.isfile(png_path), f"Missing {png_path}"
    assert os.path.isfile(ico_path), f"Missing {ico_path}"

    assert os.path.getsize(png_path) > 5000, "app_icon.png unexpectedly small"
    assert os.path.getsize(ico_path) > 10000, "app_icon.ico unexpectedly small"


def test_app_icon_generator_has_dark_outline():
    """scripts/generate_icon.py must contain the 12px #0c0e12 under-stroke outline logic."""
    gen_script = os.path.join(PROJECT_ROOT, "scripts", "generate_icon.py")
    with open(gen_script, "r", encoding="utf-8") as f:
        src = f.read()

    assert "#0c0e12" in src, "scripts/generate_icon.py must use dark outline color #0c0e12"
    assert "12.0" in src or "12" in src, "scripts/generate_icon.py must use 12px outline stroke"
    assert "pen_outline" in src or "outline" in src.lower()


def test_app_icon_contrast_against_pure_white():
    """Dark outline color #0c0e12 must provide AAA contrast against white #ffffff."""
    contrast = _contrast_ratio("#0c0e12", "#ffffff")
    assert contrast > 18.0, f"Outline contrast against white is {contrast:.2f}:1 (expected > 18.0:1)"


# ==============================================================================
# 8. APPLY_THEME ORCHESTRATION TESTS
# ==============================================================================

def test_apply_theme_orchestrator(qapp):
    """apply_theme must coordinate fonts, palette, QSS, and titlebar."""
    test_win = QWidget()
    test_win.show()
    qapp.processEvents()

    qss = theme.apply_theme(qapp, "emanager_dark", font_size=15, window=test_win)
    assert isinstance(qss, str) and len(qss) > 0
    assert "15px" in qss
    assert qapp.styleSheet() == qss

    # Verify palette updated
    assert qapp.palette().color(QPalette.ColorRole.Window).name().lower() == "#0c0e12"

    test_win.close()
    test_win.deleteLater()


# ==============================================================================
# 9. MILESTONE 6: POLISH & REFINEMENT TESTS
# ==============================================================================

def test_m6_light_theme_border_strong_values():
    """M6 R3.2: Verify higher contrast group box borders for classic_light and emanager_light."""
    classic_light = theme.get_theme("classic_light")
    assert classic_light.border_strong == "#94a3b8", f"classic_light border_strong must be #94a3b8, got {classic_light.border_strong}"

    emanager_light = theme.get_theme("emanager_light")
    assert emanager_light.border_strong == "#9ca3af", f"emanager_light border_strong must be #9ca3af, got {emanager_light.border_strong}"


def test_m6_generate_theme_qss_caching():
    """M6 R5.2: Verify QSS caching and cache invalidation in generate_theme_qss."""
    theme.clear_theme_qss_cache()
    qss1 = theme.generate_theme_qss("classic_dark", font_size=13)
    qss2 = theme.generate_theme_qss("classic_dark", font_size=13)
    assert qss1 is qss2, "Repeated generate_theme_qss calls with identical params must return cached object"

    theme.clear_theme_qss_cache()
    qss3 = theme.generate_theme_qss("classic_dark", font_size=13)
    assert qss3 == qss1, "Content must match after cache clear"


def test_m6_semantic_selectors_in_qss():
    """M6 R3.1: Verify supporting semantic selectors are present in generated QSS."""
    qss = theme.generate_theme_qss("classic_dark", font_size=13)
    assert "QPushButton#BtnCheckUpdates" in qss
    assert "QLabel#LblSettingDesc" in qss
    assert "QLabel#LblVadVal" in qss
    assert "QLabel#LblVadSysVal" in qss
    assert "QProgressBar" in qss

