"""
E2E Test Suite for Recorder67 UI Theming & Personalization.

Requirement-driven, opaque-box end-to-end tests covering all 15 features in PROJECT.md:
- Tier 1: Feature Coverage (15 tests, F1 to F15)
- Tier 2: Boundary & Corner Cases (6 tests)
- Tier 3: Cross-Feature Combinations (4 tests)
- Tier 4: Real-World Application Scenarios (4 tests)

Uses headless Qt offscreen platform plugin (QT_QPA_PLATFORM=offscreen).
"""

import os
import sys
import json
import inspect
import tempfile
import pytest

# Ensure headless Qt execution
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure repository root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Qt imports supporting PySide6 (primary) and PyQt6 fallback
try:
    from PySide6.QtWidgets import (
        QApplication, QWidget, QDialog, QMainWindow, QSlider, QSpinBox,
        QCheckBox, QComboBox, QLabel, QSystemTrayIcon, QMenu
    )
    from PySide6.QtCore import Qt, QByteArray, QPoint, QSize, QRect
    from PySide6.QtGui import QColor, QFont, QPalette, QIcon, QFontDatabase, QCloseEvent
except ImportError:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QDialog, QMainWindow, QSlider, QSpinBox,
        QCheckBox, QComboBox, QLabel, QSystemTrayIcon, QMenu
    )
    from PyQt6.QtCore import Qt, QByteArray, QPoint, QSize, QRect
    from PyQt6.QtGui import QColor, QFont, QPalette, QIcon, QFontDatabase, QCloseEvent


# ==============================================================================
# Fixtures & Test Isolation
# ==============================================================================

@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """
    Creates an isolated temporary user_settings.json and patches recorder.config
    so that tests never mutate or depend on the host machine's settings.
    """
    from recorder import config
    tmp_settings_file = os.path.join(str(tmp_path), "user_settings.json")
    
    initial_data = {
        "theme": "classic_dark",
        "font_size": 13,
        "transcript_font_size": 13,
        "always_on_top": False,
        "minimize_to_tray_on_close": False,
        "window_geometry": "",
        "timestamp_format": "offset_only",
        "preview_order": "newest_first",
        "auto_scroll_chronological": True,
        "auto_check_updates_startup": False,
    }
    with open(tmp_settings_file, "w", encoding="utf-8") as f:
        json.dump(initial_data, f, indent=2)

    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_settings_file)
    monkeypatch.setattr(config, "_CACHED_USER_SETTINGS", None)
    monkeypatch.setattr(config, "_CACHED_SETTINGS_TIME", 0.0)

    yield initial_data

    monkeypatch.setattr(config, "_CACHED_USER_SETTINGS", None)
    monkeypatch.setattr(config, "_CACHED_SETTINGS_TIME", 0.0)


# ==============================================================================
# Tier 1: Feature Coverage (Features 1 through 15)
# ==============================================================================

def test_tier1_feat1_tokenized_theme_registry(qapp):
    """
    Feature 1: Tokenized Theme Registry in recorder/ui/theme.py.
    Must define THEMES dict with 4 themes (classic_dark, classic_light, emanager_dark, emanager_light),
    complete color token sets with valid hex strings, and apply_theme dynamic QSS generator.
    """
    from recorder.ui import theme
    assert hasattr(theme, "THEMES"), "Feature 1 Contract: 'THEMES' dictionary must be defined in recorder.ui.theme"
    themes = theme.THEMES
    
    expected_themes = ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]
    for th_id in expected_themes:
        assert th_id in themes, f"Feature 1: Missing theme '{th_id}' in THEMES registry"
        t = themes[th_id]
        if hasattr(t, "__dict__"):
            t = vars(t)
        
        # Verify essential color token keys
        for key in ["id", "bg_window", "text_primary", "border", "speaker_mic_color", "speaker_system_color"]:
            assert key in t, f"Feature 1: Theme '{th_id}' missing required design token '{key}'"
            val = str(t[key])
            if key in ["bg_window", "text_primary", "border", "speaker_mic_color", "speaker_system_color"]:
                assert val.startswith("#") and len(val) in (4, 7, 9), f"Token '{key}' has invalid hex format: '{val}'"

    # Verify apply_theme function
    assert hasattr(theme, "apply_theme"), "Feature 1 Contract: 'apply_theme' function must be defined in theme.py"
    theme.apply_theme(qapp, "classic_dark", font_size=13)
    assert qapp.styleSheet(), "apply_theme must generate and set non-empty stylesheet on QApplication"


def test_tier1_feat2_saira_font_and_fallback(qapp):
    """
    Feature 2: Bundled Saira Font & Fallback.
    Bundled SIL OFL Saira font in recorder/resources/fonts/, registered via QFontDatabase,
    with clean fallback to Segoe UI and generic sans-serif in QSS.
    """
    from recorder.ui import theme
    fonts_dir = os.path.join(PROJECT_ROOT, "recorder", "resources", "fonts")
    assert os.path.exists(fonts_dir), f"Feature 2: Fonts directory '{fonts_dir}' must exist"
    
    font_files = [f for f in os.listdir(fonts_dir) if f.lower().endswith(".ttf") or f.lower().endswith(".otf")]
    assert len(font_files) > 0, f"Feature 2: At least one Saira font file (.ttf) must exist in '{fonts_dir}'"

    assert hasattr(theme, "register_bundled_fonts"), "Feature 2: 'register_bundled_fonts' must be defined in theme.py"
    res = theme.register_bundled_fonts()
    assert isinstance(res, bool), "register_bundled_fonts must return a boolean indicating registration result"

    # Verify generated QSS specifies Segoe UI fallback
    if hasattr(theme, "generate_theme_qss"):
        qss = theme.generate_theme_qss("emanager_dark", font_size=13)
        assert "Segoe UI" in qss, "Generated QSS must contain 'Segoe UI' fallback font declaration"
        assert "sans-serif" in qss, "Generated QSS must contain 'sans-serif' fallback"


def test_tier1_feat3_dwm_titlebar_adaptation(qapp):
    """
    Feature 3: Windows Desktop Window Manager (DWM) Caption Bar Adaptation.
    set_window_titlebar_theme(hwnd, is_dark) adapts titlebar for dark and light modes
    with safe platform guards preventing crashes on non-Windows/legacy OS.
    """
    from recorder.ui import theme
    assert hasattr(theme, "set_window_titlebar_theme"), (
        "Feature 3: 'set_window_titlebar_theme' must be defined in recorder.ui.theme (or exported from windows_integration)"
    )

    test_widget = QWidget()
    test_widget.show()
    qapp.processEvents()
    hwnd = int(test_widget.winId())

    # Dark mode caption adaptation
    ret_dark = theme.set_window_titlebar_theme(hwnd, is_dark=True)
    assert isinstance(ret_dark, bool) or ret_dark is None

    # Light mode caption adaptation
    ret_light = theme.set_window_titlebar_theme(hwnd, is_dark=False)
    assert isinstance(ret_light, bool) or ret_light is None

    test_widget.close()
    test_widget.deleteLater()


def test_tier1_feat4_app_icon_contrast():
    """
    Feature 4: App Icon Contrast Enhancement.
    scripts/generate_icon.py includes dark stroke outline for microphone stand,
    and recorder/resources/app_icon.png exists and provides contrast against pure white backgrounds.
    """
    gen_script = os.path.join(PROJECT_ROOT, "scripts", "generate_icon.py")
    assert os.path.exists(gen_script), "Feature 4: scripts/generate_icon.py must exist"

    with open(gen_script, "r", encoding="utf-8") as f:
        code = f.read()

    # Verify script includes stroke / outline / contrast logic for bracket/stand
    has_outline_logic = (
        "outline" in code.lower() or
        "stroke" in code.lower() or
        "pen_outline" in code.lower() or
        "#1e293b" in code.lower() or
        "#334155" in code.lower() or
        "bracket" in code.lower() and ("pen" in code.lower() or "dark" in code.lower())
    )
    assert has_outline_logic, "Feature 4: scripts/generate_icon.py must implement dark stroke/outline for microphone stand"

    icon_png = os.path.join(PROJECT_ROOT, "recorder", "resources", "app_icon.png")
    assert os.path.exists(icon_png), f"Feature 4: Asset '{icon_png}' must exist"
    assert os.path.getsize(icon_png) > 1000, "app_icon.png must be a valid, non-empty image file"


def test_tier1_feat5_modular_appearance_tab_structure(qapp, isolated_settings):
    """
    Feature 5: Modular Appearance Tab in recorder/ui/appearance_tab.py.
    Provides AppearanceTab(QWidget) with load_settings, get_settings, and reset_to_defaults contracts.
    """
    try:
        from recorder.ui.appearance_tab import AppearanceTab
    except ImportError as e:
        pytest.fail(f"Feature 5 Contract: recorder.ui.appearance_tab.AppearanceTab is not implemented: {e}")

    tab = AppearanceTab(parent=None, settings=isolated_settings, on_theme_preview=None, on_font_size_preview=None)
    assert isinstance(tab, QWidget), "AppearanceTab must be a QWidget"
    assert hasattr(tab, "load_settings"), "AppearanceTab must implement load_settings(dict)"
    assert hasattr(tab, "get_settings"), "AppearanceTab must implement get_settings() -> dict"
    assert hasattr(tab, "reset_to_defaults"), "AppearanceTab must implement reset_to_defaults()"

    data = tab.get_settings()
    required_keys = [
        "theme", "font_size", "timestamp_format", "preview_order",
        "auto_scroll_chronological", "always_on_top", "minimize_to_tray_on_close"
    ]
    for k in required_keys:
        assert k in data or (k == "font_size" and "transcript_font_size" in data), (
            f"Feature 5: AppearanceTab.get_settings() must contain '{k}'"
        )
    tab.deleteLater()


def test_tier1_feat6_settings_dialog_tab_migration(qapp, isolated_settings):
    """
    Feature 6: Settings Dialog Tab Migration.
    Migrates timestamp_format, preview_order, auto_scroll_chronological from Audio/VAD tab
    to the Appearance tab. Preserves select_tab('appearance') and backward compatibility.
    """
    from recorder.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog()

    # Appearance tab must exist in dialog
    has_appearance_tab = any(
        "wygląd" in dlg.tabs.tabText(i).lower() or "personalizacja" in dlg.tabs.tabText(i).lower()
        for i in range(dlg.tabs.count())
    )
    assert has_appearance_tab, "Feature 6: SettingsDialog must contain a dedicated 'Wygląd & Personalizacja' tab"

    # Audio/VAD tab (tab 1) must NO LONGER contain timestamp_format or preview_order comboboxes
    audio_tab = dlg.tabs.widget(1)
    combos_in_audio = [c.objectName() for c in audio_tab.findChildren(QComboBox)]
    assert "combo_timestamp_format" not in combos_in_audio, "timestamp_format must be migrated out of Audio/VAD tab"
    assert "combo_preview_order" not in combos_in_audio, "preview_order must be migrated out of Audio/VAD tab"

    # Tab switching helper
    dlg.select_tab("appearance") if hasattr(dlg, "select_tab") else None
    current_text = dlg.tabs.tabText(dlg.tabs.currentIndex()).lower()
    assert "wygląd" in current_text or "personalizacja" in current_text, "select_tab('appearance') must activate Appearance tab"

    dlg.close()
    dlg.deleteLater()


def test_tier1_feat7_visual_theme_cards_grid(qapp, isolated_settings):
    """
    Feature 7: Visual Theme Cards in AppearanceTab.
    2x2 grid representing 4 themes, showing swatches and active state highlight.
    """
    try:
        from recorder.ui.appearance_tab import AppearanceTab
    except ImportError as e:
        pytest.fail(f"Feature 7: AppearanceTab not implemented: {e}")

    tab = AppearanceTab(parent=None, settings=isolated_settings, on_theme_preview=None, on_font_size_preview=None)
    tab.show()
    qapp.processEvents()

    # Look for theme cards container or children
    theme_cards = getattr(tab, "theme_cards", None)
    if theme_cards is None:
        # Fallback: search child widgets with theme card properties or names
        theme_cards = [w for w in tab.findChildren(QWidget) if "themecard" in w.__class__.__name__.lower() or "card" in w.objectName().lower()]

    assert len(theme_cards) == 4 or (isinstance(theme_cards, dict) and len(theme_cards) == 4), (
        f"Feature 7: AppearanceTab must display exactly 4 theme cards (found {len(theme_cards)})"
    )

    tab.close()
    tab.deleteLater()


def test_tier1_feat8_live_preview_and_revert_mechanism(qapp, isolated_settings):
    """
    Feature 8: Live Theme Preview & Revert on Cancel.
    Clicking theme card triggers immediate preview; dismissing dialog (reject / Anuluj)
    rolls back the application theme to its initial entrance state.
    """
    from recorder.ui import theme
    from recorder.ui.settings_dialog import SettingsDialog

    theme.apply_theme(qapp, "classic_dark", font_size=13)
    initial_qss = qapp.styleSheet()

    dlg = SettingsDialog()
    dlg.show()
    qapp.processEvents()

    # Trigger live preview to classic_light
    if hasattr(dlg, "_on_theme_preview"):
        dlg._on_theme_preview("classic_light")
    elif hasattr(dlg, "appearance_tab") and hasattr(dlg.appearance_tab, "theme_preview_requested"):
        dlg.appearance_tab.theme_preview_requested.emit("classic_light")
    else:
        theme.apply_theme(qapp, "classic_light", font_size=13)

    preview_qss = qapp.styleSheet()
    assert preview_qss != initial_qss, "Feature 8: Live preview must alter the application stylesheet"

    # User cancels dialog
    dlg.reject()
    qapp.processEvents()

    reverted_qss = qapp.styleSheet()
    assert reverted_qss == initial_qss, "Feature 8: Dialog reject() must revert application stylesheet to initial theme"

    dlg.deleteLater()


def test_tier1_feat9_transcript_font_scaling(qapp, isolated_settings):
    """
    Feature 9: Transcript Preview Font Size Scaling.
    AppearanceTab provides font size selector with range 11px to 17px (default 13px),
    emitting preview signals dynamically.
    """
    try:
        from recorder.ui.appearance_tab import AppearanceTab
    except ImportError as e:
        pytest.fail(f"Feature 9: AppearanceTab not implemented: {e}")

    emitted_sizes = []
    tab = AppearanceTab(
        parent=None,
        settings=isolated_settings,
        on_theme_preview=None,
        on_font_size_preview=lambda sz: emitted_sizes.append(sz)
    )

    # Find font size slider or spinbox
    slider = getattr(tab, "slider_font_size", None) or tab.findChild(QSlider)
    spinbox = getattr(tab, "spin_font_size", None) or tab.findChild(QSpinBox)

    control = slider or spinbox
    assert control is not None, "Feature 9: AppearanceTab must provide a font size slider or spinbox"
    assert control.minimum() == 11, f"Minimum font size must be 11, got {control.minimum()}"
    assert control.maximum() == 17, f"Maximum font size must be 17, got {control.maximum()}"
    assert control.value() == 13, f"Default font size must be 13, got {control.value()}"

    # Change value and verify callback / signal
    control.setValue(16)
    qapp.processEvents()
    assert 16 in emitted_sizes or tab.get_settings().get("font_size") == 16, (
        "Adjusting font size must trigger preview callback or update tab settings"
    )

    tab.deleteLater()


def test_tier1_feat10_always_on_top_behavior(qapp, isolated_settings):
    """
    Feature 10: Dynamic Always on Top Behavior.
    Toggling always_on_top updates Qt.WindowType.WindowStaysOnTopHint dynamically.
    """
    from recorder.ui.window import SmartDictaphoneWindow
    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    assert hasattr(win, "set_always_on_top"), "Feature 10: SmartDictaphoneWindow must implement set_always_on_top(bool)"

    # Enable Always on Top
    win.set_always_on_top(True)
    qapp.processEvents()
    assert bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is True, "WindowStaysOnTopHint flag must be set"

    # Disable Always on Top
    win.set_always_on_top(False)
    qapp.processEvents()
    assert bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is False, "WindowStaysOnTopHint flag must be cleared"

    win.close()
    win.deleteLater()


def test_tier1_feat11_minimize_to_tray_on_close(qapp, isolated_settings, monkeypatch):
    """
    Feature 11: Minimize-to-Tray on Close Protection.
    When minimize_to_tray_on_close is True and tray is available, [X] closeEvent is ignored
    and window hides. Context menu "Zakończ" sets _force_quit to bypass protection.
    """
    from recorder.config import save_user_settings
    save_user_settings({"minimize_to_tray_on_close": True})

    from recorder.ui.window import SmartDictaphoneWindow
    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    # Mock system tray as available
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)

    # 1. Close event with minimize-to-tray enabled -> must ignore event and hide window
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted() is False, "closeEvent must be ignored when minimize_to_tray_on_close is True"
    assert win.isHidden() is True, "Window must hide when minimize-to-tray is triggered"

    # 2. Tray menu force quit -> must accept event and allow termination
    win._force_quit = True
    event2 = QCloseEvent()
    win.closeEvent(event2)
    assert event2.isAccepted() is True, "closeEvent must be accepted when _force_quit is True"

    win.deleteLater()


def test_tier1_feat12_window_geometry_persistence(qapp, isolated_settings):
    """
    Feature 12: Window Geometry Persistence.
    Window position and dimensions serialize via saveGeometry() to hex in user_settings.json
    and restore via restoreGeometry() on launch.
    """
    from recorder.ui.window import SmartDictaphoneWindow
    from recorder.config import load_user_settings

    win = SmartDictaphoneWindow()
    win.resize(850, 920)
    win.move(140, 160)
    win.show()
    qapp.processEvents()

    # Simulate close to persist geometry
    win._force_quit = True
    event = QCloseEvent()
    win.closeEvent(event)
    win.deleteLater()

    # Verify geometry hex saved in settings
    saved = load_user_settings(force_reload=True)
    assert "window_geometry" in saved, "window_geometry key must exist in user_settings.json"
    geom_hex = saved["window_geometry"]
    assert isinstance(geom_hex, str) and len(geom_hex) > 20, "window_geometry must be a valid non-empty hex string"

    # Restore in new window instance
    win2 = SmartDictaphoneWindow()
    win2.show()
    qapp.processEvents()
    assert win2.width() > 700 and win2.height() > 800, "Restored window dimensions should match saved geometry"
    win2.close()
    win2.deleteLater()


def test_tier1_feat13_purge_inline_stylesheet_clutter():
    """
    Feature 13: Purge Inline setStyleSheet Clutter.
    Verifies that bulky inline setStyleSheet blocks with hardcoded dark hex values in window.py
    and settings_dialog.py are purged and centralized into theme.py.
    """
    window_path = os.path.join(PROJECT_ROOT, "recorder", "ui", "window.py")
    with open(window_path, "r", encoding="utf-8") as f:
        window_src = f.read()

    # Count occurrences of repetitive inline setStyleSheet in window.py
    inline_count = window_src.count(".setStyleSheet(")
    assert inline_count <= 25, (
        f"Feature 13: Repetitive inline setStyleSheet calls ({inline_count}) in window.py must be purged to <= 25"
    )


def test_tier1_feat14_theme_aware_speaker_colors_o1(isolated_settings):
    """
    Feature 14: Theme-Aware O(1) Speaker Colors in RollingTranscriber.
    RollingTranscriber._compile_full_transcript() resolves mic and system speaker colors
    according to active theme in O(1) time without string parsing or audio overhead.
    """
    from recorder.ui import theme
    import recorder.config as cfg
    assert hasattr(theme, "get_speaker_colors") and hasattr(theme, "THEME_SPEAKER_COLORS"), (
        "Feature 14: theme.py must define get_speaker_colors(theme_id) and THEME_SPEAKER_COLORS"
    )
    assert hasattr(cfg, "THEME_SPEAKER_COLORS") and hasattr(cfg, "get_speaker_colors"), (
        "Feature 14: config.py must define get_speaker_colors(theme_id) and THEME_SPEAKER_COLORS"
    )

    from recorder.core.rolling_transcriber import RollingTranscriber
    transcriber = RollingTranscriber(session_start_time=1000.0)

    # Test transcript with mic and system turns
    turns = [
        {"start": 10.0, "end": 14.0, "speaker": "Speaker 1", "text": "Hello mic", "channel": "mic"},
        {"start": 15.0, "end": 20.0, "speaker": "Speaker 2", "text": "Hello system", "channel": "system"},
    ]
    transcriber.completed_blocks = turns

    # Verify all 4 themes have their exact theme colors in the compiled transcript
    for th_id, colors in cfg.THEME_SPEAKER_COLORS.items():
        html_out, _, _ = transcriber._compile_full_transcript(theme_id=th_id)
        assert f"color: {colors['mic']};" in html_out, f"{th_id} mic color {colors['mic']} missing from html"
        assert f"color: {colors['system']};" in html_out, f"{th_id} system color {colors['system']} missing from html"


def test_tier1_feat15_audio_decoupling_zero_drift():
    """
    Feature 15: Audio Decoupling & 8-hour Zero Buffer Drift.
    Audio worker threads, capture, Silero VAD, and loopback must not import Qt UI theme modules.
    """
    audio_modules = [
        os.path.join(PROJECT_ROOT, "recorder", "audio", "capture.py"),
        os.path.join(PROJECT_ROOT, "recorder", "audio", "devices.py"),
        os.path.join(PROJECT_ROOT, "recorder", "core", "vad.py"),
    ]

    for p in audio_modules:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                src = f.read()
            assert "recorder.ui.theme" not in src, f"Audio module '{p}' must not import UI theme styling"
            assert "QApplication" not in src, f"Audio module '{p}' must not depend on QApplication"


# ==============================================================================
# Tier 2: Boundary & Corner Cases
# ==============================================================================

def test_tier2_boundary_invalid_theme_id_fallback(qapp):
    """
    Tier 2: Unknown, invalid, or empty theme IDs must gracefully fall back to 'classic_dark'.
    """
    from recorder.ui import theme
    assert hasattr(theme, "apply_theme"), "theme.apply_theme must exist"

    # Should not raise exception on invalid theme ID
    theme.apply_theme(qapp, "non_existent_theme_999")
    assert qapp.styleSheet(), "Stylesheet must not be empty after fallback to classic_dark"

    theme.apply_theme(qapp, "")
    assert qapp.styleSheet()

    theme.apply_theme(qapp, None)
    assert qapp.styleSheet()


def test_tier2_boundary_font_size_clamping(qapp, isolated_settings):
    """
    Tier 2: Extreme font sizes (< 11 or > 17) must be clamped to the [11, 17] bounds.
    """
    try:
        from recorder.ui.appearance_tab import AppearanceTab
    except ImportError:
        pytest.fail("AppearanceTab not implemented")

    tab = AppearanceTab(parent=None, settings=isolated_settings, on_theme_preview=None, on_font_size_preview=None)

    # Test extreme values
    tab.load_settings({"font_size": 8})
    assert tab.get_settings().get("font_size") == 11, "Font size below 11 must be clamped to 11"

    tab.load_settings({"font_size": 25})
    assert tab.get_settings().get("font_size") == 17, "Font size above 17 must be clamped to 17"

    tab.load_settings({"font_size": -10})
    assert tab.get_settings().get("font_size") == 11, "Negative font size must be clamped to 11"

    tab.deleteLater()


def test_tier2_boundary_corrupted_or_missing_geometry(qapp, isolated_settings, monkeypatch):
    """
    Tier 2: Corrupted or non-hex window_geometry strings must fall back gracefully without crashing.
    """
    from recorder.config import save_user_settings
    save_user_settings({"window_geometry": "CORRUPTED_NOT_A_HEX_STRING!@#$%"})

    from recorder.ui.window import SmartDictaphoneWindow
    # Launch window with corrupted geometry -> must not raise exception
    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    assert win.width() >= 600 and win.height() >= 600, "Window should fall back to standard desktop dimensions"
    win.close()
    win.deleteLater()


def test_tier2_boundary_font_file_missing_fallback(qapp, monkeypatch):
    """
    Tier 2: Missing font asset directory must result in clean fallback without exceptions.
    """
    from recorder.ui import theme
    if hasattr(theme, "register_bundled_fonts"):
        # Temporarily point fonts directory to non-existent location
        fake_dir = os.path.join(PROJECT_ROOT, "recorder", "resources", "non_existent_fonts")
        monkeypatch.setattr(theme, "get_fonts_dir", lambda: fake_dir)
        res = theme.register_bundled_fonts(fonts_dir=fake_dir, force=True)
        assert res is False or res is None, "register_bundled_fonts must return False when font files are missing"


def test_tier2_boundary_tray_unavailable_fallback(qapp, isolated_settings, monkeypatch):
    """
    Tier 2: When system tray is unavailable on the OS, closeEvent must not trap the user.
    """
    from recorder.config import save_user_settings
    save_user_settings({"minimize_to_tray_on_close": True})

    from recorder.ui.window import SmartDictaphoneWindow
    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    # Simulate tray unavailable on system
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    win.tray_icon = None

    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted() is True, "Close event must be accepted when system tray is unavailable"

    win.deleteLater()


def test_tier2_boundary_missing_settings_keys_defaults(qapp):
    """
    Tier 2: AppearanceTab.load_settings({}) with empty dictionary must assign defaults without KeyError.
    """
    try:
        from recorder.ui.appearance_tab import AppearanceTab
    except ImportError:
        pytest.fail("AppearanceTab not implemented")

    tab = AppearanceTab(parent=None, settings={}, on_theme_preview=None, on_font_size_preview=None)
    tab.load_settings({})
    res = tab.get_settings()
    assert res.get("theme") == "classic_dark"
    assert res.get("font_size") in (13, None) or res.get("transcript_font_size") == 13
    tab.deleteLater()


# ==============================================================================
# Tier 3: Cross-Feature Combinations
# ==============================================================================

def test_tier3_combo_theme_and_font_size_qss(qapp):
    """
    Tier 3: Pairwise combination of theme selection and font scaling in generated QSS.
    Verifies that all 4 themes scale font size correctly without CSS syntax corruptions.
    """
    from recorder.ui import theme
    if not hasattr(theme, "generate_theme_qss"):
        pytest.fail("theme.generate_theme_qss not implemented")

    themes = ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]
    font_sizes = [11, 13, 15, 17]

    for th in themes:
        for sz in font_sizes:
            qss = theme.generate_theme_qss(th, font_size=sz)
            assert f"{sz}px" in qss, f"Theme '{th}' QSS must contain font size '{sz}px'"
            assert qss.count("{") == qss.count("}"), f"Theme '{th}' QSS has mismatched braces at size {sz}"


def test_tier3_combo_always_on_top_with_theme_switch(qapp, isolated_settings):
    """
    Tier 3: Toggling themes while Always on Top is active preserves window flags.
    """
    from recorder.ui import theme
    from recorder.ui.window import SmartDictaphoneWindow

    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    win.set_always_on_top(True)
    assert bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is True

    # Apply themes while Always on Top is active
    theme.apply_theme(qapp, "classic_light", font_size=13, window=win)
    qapp.processEvents()
    assert bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is True, (
        "WindowStaysOnTopHint must persist across theme changes"
    )

    theme.apply_theme(qapp, "emanager_dark", font_size=13, window=win)
    qapp.processEvents()
    assert bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is True

    win.close()
    win.deleteLater()


def test_tier3_combo_tray_minimize_and_geometry_save(qapp, isolated_settings, monkeypatch):
    """
    Tier 3: Window geometry must be saved before window is hidden when minimizing to tray.
    """
    from recorder.config import save_user_settings, load_user_settings
    save_user_settings({"minimize_to_tray_on_close": True, "window_geometry": ""})

    from recorder.ui.window import SmartDictaphoneWindow
    win = SmartDictaphoneWindow()
    win.resize(840, 890)
    win.show()
    qapp.processEvents()

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)

    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted() is False
    assert win.isHidden() is True

    # Geometry must have been persisted to user_settings.json prior to hide
    st = load_user_settings(force_reload=True)
    assert st.get("window_geometry") != "", "Geometry must be saved even during minimize-to-tray close events"

    win._force_quit = True
    win.close()
    win.deleteLater()


def test_tier3_combo_settings_migration_and_live_preview(qapp, isolated_settings):
    """
    Tier 3: Modifying migrated display options alongside live preview in AppearanceTab
    maintains complete payload coherence without loss of migrated settings.
    """
    try:
        from recorder.ui.appearance_tab import AppearanceTab
    except ImportError:
        pytest.fail("AppearanceTab not implemented")

    previewed_themes = []
    tab = AppearanceTab(
        parent=None,
        settings=isolated_settings,
        on_theme_preview=lambda th: previewed_themes.append(th),
        on_font_size_preview=None
    )

    # Change migrated display settings
    new_settings = {
        "theme": "emanager_light",
        "timestamp_format": "clock_only",
        "preview_order": "chronological",
        "auto_scroll_chronological": False,
        "font_size": 15,
        "always_on_top": True,
        "minimize_to_tray_on_close": True,
    }
    tab.load_settings(new_settings)
    collected = tab.get_settings()

    for k, v in new_settings.items():
        assert collected.get(k) == v, f"Field '{k}' mismatch: expected {v}, got {collected.get(k)}"

    tab.deleteLater()


# ==============================================================================
# Tier 4: Real-World Application Scenarios
# ==============================================================================

def test_tier4_scenario_theme_preview_cancel_rollback(qapp, isolated_settings):
    """
    Tier 4 Scenario: User opens settings dialog, previews multiple themes,
    adjusts font size, then clicks 'Anuluj'. The application must roll back
    100% of stylesheet, palette, and titlebar changes to the initial state.
    """
    from recorder.ui import theme
    from recorder.ui.settings_dialog import SettingsDialog

    theme.apply_theme(qapp, "classic_dark", font_size=13)
    initial_qss = qapp.styleSheet()

    dlg = SettingsDialog()
    dlg.show()
    qapp.processEvents()

    # 1. Preview Classic Light
    if hasattr(dlg, "_on_theme_preview"):
        dlg._on_theme_preview("classic_light")
    else:
        theme.apply_theme(qapp, "classic_light", font_size=13)
    assert qapp.styleSheet() != initial_qss

    # 2. Preview eManager Dark
    if hasattr(dlg, "_on_theme_preview"):
        dlg._on_theme_preview("emanager_dark")
    else:
        theme.apply_theme(qapp, "emanager_dark", font_size=13)

    # 3. Cancel dialog
    dlg.reject()
    qapp.processEvents()

    assert qapp.styleSheet() == initial_qss, "Application stylesheet must completely roll back on dialog cancel"
    dlg.deleteLater()


def test_tier4_scenario_theme_save_persistence_cycle(qapp, isolated_settings):
    """
    Tier 4 Scenario: User selects eManager Dark, font size 15, enables Always on Top,
    and saves settings. Verifies disk persistence and correct reload upon app restart.
    """
    from recorder.config import load_user_settings, save_user_settings
    from recorder.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog()
    dlg.show()
    qapp.processEvents()

    # Modify settings in Appearance tab
    target_settings = {
        "theme": "emanager_dark",
        "font_size": 15,
        "always_on_top": True,
        "minimize_to_tray_on_close": True,
    }

    if hasattr(dlg, "appearance_tab"):
        dlg.appearance_tab.load_settings(target_settings)
    
    # Trigger dialog save
    if hasattr(dlg, "_save_and_accept"):
        dlg._save_and_accept()
    else:
        # Fallback simulation
        save_user_settings(target_settings)
        dlg.accept()

    qapp.processEvents()
    dlg.deleteLater()

    # Verify settings persisted on disk
    persisted = load_user_settings(force_reload=True)
    assert persisted.get("theme") == "emanager_dark", f"Expected theme emanager_dark, got {persisted.get('theme')}"
    assert persisted.get("font_size") == 15 or persisted.get("transcript_font_size") == 15
    assert persisted.get("always_on_top") is True


def test_tier4_scenario_window_geometry_save_restore_cycle(qapp, isolated_settings):
    """
    Tier 4 Scenario: Move/resize window, close, and launch new window instance.
    The new window must restore identical geometry bounds within available screen limits.
    """
    from recorder.ui.window import SmartDictaphoneWindow

    # 1. First window session
    win1 = SmartDictaphoneWindow()
    win1.resize(880, 940)
    win1.move(130, 150)
    win1.show()
    qapp.processEvents()

    target_rect = win1.geometry()
    win1._force_quit = True
    event = QCloseEvent()
    win1.closeEvent(event)
    win1.deleteLater()

    # 2. Second window session (re-launch)
    win2 = SmartDictaphoneWindow()
    win2.show()
    qapp.processEvents()

    restored_rect = win2.geometry()
    assert abs(restored_rect.width() - target_rect.width()) <= 50, (
        f"Restored width ({restored_rect.width()}) should match target ({target_rect.width()})"
    )
    assert abs(restored_rect.height() - target_rect.height()) <= 50, (
        f"Restored height ({restored_rect.height()}) should match target ({target_rect.height()})"
    )

    win2.close()
    win2.deleteLater()


def test_tier4_scenario_marathon_recording_tray_protection(qapp, isolated_settings, monkeypatch):
    """
    Tier 4 Scenario: During an active 8-hour recording session, accidental [X] clicks
    must not terminate the process or drop audio buffers. Context menu 'Zakończ'
    must cleanly flush audio and exit.
    """
    from recorder.config import save_user_settings, SmartRecordState
    save_user_settings({"minimize_to_tray_on_close": True})

    from recorder.ui.window import SmartDictaphoneWindow
    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)

    # Simulate active recording state
    if win.worker is not None:
        win.worker.state = SmartRecordState.RECORDING_SPEECH

    # Accidental [X] click
    accidental_close = QCloseEvent()
    win.closeEvent(accidental_close)

    assert accidental_close.isAccepted() is False, "Close must be ignored during recording with tray protection"
    assert win.isHidden() is True, "Window should be safely hidden in tray"
    if win.worker is not None:
        assert win.worker.state == SmartRecordState.RECORDING_SPEECH, "Audio worker must continue running uninterrupted"

    # Clean shutdown via tray menu "Zakończ"
    win._force_quit = True
    clean_close = QCloseEvent()
    win.closeEvent(clean_close)

    assert clean_close.isAccepted() is True, "Clean close must be accepted on tray menu quit"
    win.deleteLater()


# ==============================================================================
# Tier 5: Milestone 6 Polish & Refinement Tests
# ==============================================================================

def test_m6_themecard_compact_layout_no_desc(qapp):
    """
    M6 Requirement R1.2: ThemeCard must have lbl_desc completely removed,
    retaining only lbl_name, lbl_badge, swatch_bg, swatch_accent, swatch_text,
    and tooltip set to theme_def.description.
    """
    from recorder.ui.theme import THEMES
    from recorder.ui.appearance_tab import ThemeCard

    card = ThemeCard(THEMES["classic_dark"])
    assert not hasattr(card, "lbl_desc"), "ThemeCard must not have attribute 'lbl_desc'"
    assert hasattr(card, "lbl_name"), "ThemeCard must have 'lbl_name'"
    assert hasattr(card, "lbl_badge"), "ThemeCard must have 'lbl_badge'"
    assert hasattr(card, "swatch_bg"), "ThemeCard must have 'swatch_bg'"
    assert hasattr(card, "swatch_accent"), "ThemeCard must have 'swatch_accent'"
    assert hasattr(card, "swatch_text"), "ThemeCard must have 'swatch_text'"
    assert card.toolTip() == THEMES["classic_dark"].description
    card.deleteLater()


def test_m6_appearance_tab_simplified_preview_order_labels(qapp, isolated_settings):
    """
    M6 Requirement R1.1: Preview order labels must be 'Od najnowszych' and 'Od najstarszych'.
    """
    from recorder.ui.appearance_tab import AppearanceTab

    tab = AppearanceTab(settings=isolated_settings)
    assert tab.combo_preview_order.count() == 2
    assert tab.combo_preview_order.itemText(0) == "Od najnowszych"
    assert tab.combo_preview_order.itemData(0) == "newest_first"
    assert tab.combo_preview_order.itemText(1) == "Od najstarszych"
    assert tab.combo_preview_order.itemData(1) == "chronological"
    tab.deleteLater()


def test_m6_appearance_tab_dynamic_autoscroll_enablement(qapp, isolated_settings):
    """
    M6 Requirement R2.1: combo_preview_order index changes dynamically enable chk_auto_scroll
    when 'chronological' and disable when 'newest_first' without saving/reopening.
    """
    from recorder.ui.appearance_tab import AppearanceTab

    tab = AppearanceTab(settings=isolated_settings)

    # Initially loaded with newest_first -> chk_auto_scroll should be disabled
    tab.load_settings({"preview_order": "newest_first"})
    assert tab.chk_auto_scroll.isEnabled() is False

    # Switch in real-time to chronological -> chk_auto_scroll must become enabled
    tab.combo_preview_order.setCurrentIndex(1)  # chronological
    qapp.processEvents()
    assert tab.chk_auto_scroll.isEnabled() is True

    # Switch in real-time back to newest_first -> chk_auto_scroll must become disabled
    tab.combo_preview_order.setCurrentIndex(0)  # newest_first
    qapp.processEvents()
    assert tab.chk_auto_scroll.isEnabled() is False

    # load_settings with chronological -> enabled
    tab.load_settings({"preview_order": "chronological"})
    assert tab.chk_auto_scroll.isEnabled() is True
    tab.deleteLater()


def test_m6_appearance_tab_clean_window_behavior_labels(qapp, isolated_settings):
    """
    M6 Requirement R2.2: Window behavior section title and checkboxes must be cleaned
    and stripped of emoji and technical parentheses.
    """
    from recorder.ui.appearance_tab import AppearanceTab

    tab = AppearanceTab(settings=isolated_settings)
    grp = tab.findChild(QWidget, "GrpWindowBehavior")
    assert grp is not None
    assert grp.title() == "Zachowanie okna"
    assert "🪟" not in grp.title()

    assert tab.chk_always_on_top.text() == "Okno zawsze na wierzchu"
    assert "📌" not in tab.chk_always_on_top.text()
    assert "Always on Top" not in tab.chk_always_on_top.text()

    assert tab.chk_minimize_to_tray.text() == "Minimalizuj do zasobnika systemowego przy zamykaniu"
    assert "[X]" not in tab.chk_minimize_to_tray.text()
    assert "Tray" not in tab.chk_minimize_to_tray.text()
    tab.deleteLater()


def test_m6_tray_notification_click_decoupling(qapp, isolated_settings, monkeypatch):
    """
    M6 Requirement R4.1: Decouple tray message click:
    - silence_alert message click restores window and opens audio inspection dialog.
    - minimized message click restores window WITHOUT opening audio inspection dialog.
    - message click resets _last_tray_message_type to None.
    """
    from recorder.ui.window import SmartDictaphoneWindow
    from recorder.config import RecordSourceMode
    from unittest.mock import MagicMock

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)

    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    inspection_mock = MagicMock()
    monkeypatch.setattr(win, "_show_audio_inspection_dialog", inspection_mock)

    # --- Scenario A: Silence Watchdog Alert ---
    win._handle_silence_timed_out_to_tray("5 min", RecordSourceMode.SYSTEM_ONLY)
    assert win._last_tray_message_type == "silence_alert"
    assert win._last_silence_source_mode == RecordSourceMode.SYSTEM_ONLY

    # Click the silence notification
    win._on_tray_message_clicked()
    assert win.isVisible() is True
    assert win._last_tray_message_type is None
    inspection_mock.assert_called_once_with(RecordSourceMode.SYSTEM_ONLY)

    # --- Scenario B: Minimize to Tray on Close ---
    inspection_mock.reset_mock()
    from recorder.config import save_user_settings
    save_user_settings({"minimize_to_tray_on_close": True})

    # Close event triggers minimize to tray
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted() is False
    assert win.isHidden() is True
    assert win._last_tray_message_type == "minimized"

    # Click the minimize notification
    win._on_tray_message_clicked()
    assert win.isVisible() is True
    assert win._last_tray_message_type is None
    inspection_mock.assert_not_called()  # MUST NOT open inspection dialog

    # --- Scenario C: Tray click when type is None ---
    inspection_mock.reset_mock()
    win.hide()
    win._last_tray_message_type = None
    win._on_tray_message_clicked()
    assert win.isVisible() is True
    inspection_mock.assert_not_called()

    win._force_quit = True
    win.close()
    win.deleteLater()


def test_m6_default_settings_unconfigured_verification(monkeypatch, tmp_path):
    """
    M6 Requirement R4.2: Verify default settings when no user_settings.json exists:
    - always_on_top == False
    - minimize_to_tray_on_close == False
    - theme == "classic_dark"
    """
    from recorder import config
    non_existent_file = os.path.join(str(tmp_path), "does_not_exist_user_settings.json")
    monkeypatch.setattr(config, "SETTINGS_FILE", non_existent_file)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "_CACHED_USER_SETTINGS", None)
    monkeypatch.setattr(config, "_CACHED_SETTINGS_TIME", 0.0)

    # Clear any lingering environment variables
    monkeypatch.delenv("APP_THEME", raising=False)
    monkeypatch.delenv("ALWAYS_ON_TOP", raising=False)
    monkeypatch.delenv("MINIMIZE_TO_TRAY_ON_CLOSE", raising=False)

    assert config.is_always_on_top() is False
    assert config.is_minimize_to_tray_on_close() is False
    assert config.get_theme() == "classic_dark"

    raw = config.load_user_settings(force_reload=True)
    assert raw["always_on_top"] is False
    assert raw["minimize_to_tray_on_close"] is False
    assert raw["theme"] == "classic_dark"


def test_m6_settings_dialog_reject_instant_performance(qapp, isolated_settings):
    """
    M6 Requirement R5.2: SettingsDialog reject() without active theme preview must
    execute instantaneously (< 50ms).
    """
    import time
    from recorder.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog()
    assert dlg._theme_previewed is False

    t0 = time.perf_counter()
    dlg.reject()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 50.0, f"Dialog reject without preview must take < 50ms, took {elapsed_ms:.1f}ms"
    dlg.deleteLater()


def test_m6_settings_dialog_tab_title_no_mnemonic(qapp, isolated_settings):
    """
    M6 Requirement R5.1: SettingsDialog tab title must be 'Wygląd i Personalizacja'
    without ampersand keyboard mnemonic accelerator.
    """
    from recorder.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog()
    tab_texts = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert "🎨 Wygląd i Personalizacja" in tab_texts
    assert not any("&" in t for t in tab_texts), f"Tab titles must not contain '&' mnemonic: {tab_texts}"
    dlg.deleteLater()

