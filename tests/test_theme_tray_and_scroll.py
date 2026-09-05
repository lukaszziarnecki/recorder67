"""
Testy integracji powiadomień zasobnika systemowego i synchronizacji auto-scrolla.

Zakres:
1. Rozdzielenie akcji kliknięcia powiadomień traya:
   - Szybkie naprzemienne powiadomienia ('minimized' -> 'silence_alert')
   - Kliknięcia przy braku aktywnego typu powiadomienia (_last_tray_message_type is None)
   - Wielokrotne kliknięcia w ten sam dymek (idempotencja, brak powielonych dialogów)
   - Weryfikacja: powiadomienie o ukryciu do traya przywraca okno i NIE otwiera dialogu inspekcji audio
2. Dynamiczne stany auto-scrolla w zależności od kolejności podglądu:
   - Dynamiczne blokowanie/odblokowywanie checkboxa bez przeładowywania okna
   - Zachowanie stanu zaznaczenia (isChecked) przy przełączaniu opcji
   - Spójność przywracania ustawień domyślnych (reset_to_defaults)
"""

import os
import sys
import pytest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtCore import Qt
except ImportError:
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox
    from PyQt6.QtGui import QCloseEvent
    from PyQt6.QtCore import Qt

from recorder.ui.window import SmartDictaphoneWindow
from recorder.ui.appearance_tab import AppearanceTab
from recorder.ui.settings_dialog import SettingsDialog
from recorder.config import RecordSourceMode, save_user_settings


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    from recorder import config
    settings_file = os.path.join(str(tmp_path), "user_settings.json")
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "_CACHED_USER_SETTINGS", None)
    monkeypatch.setattr(config, "_CACHED_SETTINGS_TIME", 0.0)
    save_user_settings({
        "theme": "classic_dark",
        "font_size": 13,
        "preview_order": "newest_first",
        "auto_scroll_chronological": True,
        "always_on_top": False,
        "minimize_to_tray_on_close": False,
    })
    return {
        "theme": "classic_dark",
        "font_size": 13,
        "preview_order": "newest_first",
        "auto_scroll_chronological": True,
        "always_on_top": False,
        "minimize_to_tray_on_close": False,
    }


# =============================================================================
# 1. TRAY MESSAGE CLICK DECOUPLING STRESS-TESTS
# =============================================================================

def test_adv_tray_rapid_alternating_notifications(qapp, isolated_settings, monkeypatch):
    """
    Stress-test rapid alternating notifications (minimized -> silence_alert -> minimized)
    across 50 iterations:
    - Verifies _restore_from_tray is called 100% of the time.
    - Verifies _show_audio_inspection_dialog is NEVER called on 'minimized'.
    - Verifies _show_audio_inspection_dialog is ALWAYS called on 'silence_alert' with exact source mode.
    - Verifies _last_tray_message_type is always consumed to None on click.
    """
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    save_user_settings({"minimize_to_tray_on_close": True})

    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    mock_inspection = MagicMock()
    monkeypatch.setattr(win, "_show_audio_inspection_dialog", mock_inspection)

    mock_restore = MagicMock(wraps=win._restore_from_tray)
    monkeypatch.setattr(win, "_restore_from_tray", mock_restore)

    total_restore_calls = 0
    total_inspection_calls = 0

    sources = [RecordSourceMode.MIC_ONLY, RecordSourceMode.SYSTEM_ONLY, RecordSourceMode.HYBRID_DUAL]

    for i in range(50):
        # Step A: Trigger minimize to tray
        ev = QCloseEvent()
        win.closeEvent(ev)
        assert ev.isAccepted() is False
        assert win.isHidden() is True
        assert win._last_tray_message_type == "minimized"

        # User clicks minimized balloon notification
        win._on_tray_message_clicked()
        total_restore_calls += 1
        assert mock_restore.call_count == total_restore_calls
        assert mock_inspection.call_count == total_inspection_calls  # MUST NOT INCREASE
        assert win._last_tray_message_type is None

        # Step B: Trigger silence alert notification
        src = sources[i % len(sources)]
        win._handle_silence_timed_out_to_tray(f"{i + 1}m", src)
        assert win._last_tray_message_type == "silence_alert"
        assert win._last_silence_source_mode == src

        # User clicks silence alert balloon notification
        win._on_tray_message_clicked()
        total_restore_calls += 1
        total_inspection_calls += 1
        assert mock_restore.call_count == total_restore_calls
        assert mock_inspection.call_count == total_inspection_calls
        mock_inspection.assert_called_with(src)
        assert win._last_tray_message_type is None

    win._force_quit = True
    win.close()
    win.deleteLater()


def test_adv_tray_click_when_none_or_timed_out(qapp, isolated_settings, monkeypatch):
    """
    Test clicks arriving after notification timed out or when _last_tray_message_type is None.
    - Clicking with None must restore window and NEVER open inspection dialog.
    - Multiple consecutive clicks on None must remain safe and idempotent.
    """
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)

    win = SmartDictaphoneWindow()
    win.hide()
    qapp.processEvents()

    mock_inspection = MagicMock()
    monkeypatch.setattr(win, "_show_audio_inspection_dialog", mock_inspection)

    # Explicitly ensure None
    win._last_tray_message_type = None

    for _ in range(10):
        win._on_tray_message_clicked()
        assert win.isVisible() is True
        assert win._last_tray_message_type is None
        mock_inspection.assert_not_called()

    win._force_quit = True
    win.close()
    win.deleteLater()


def test_adv_tray_multiple_consecutive_clicks_on_same_balloon(qapp, isolated_settings, monkeypatch):
    """
    Test multiple consecutive clicks on the SAME balloon notification:
    - For silence_alert: 1st click restores & inspects; subsequent clicks restore but DO NOT inspect again.
    - For minimized: all clicks restore, NONE inspects.
    """
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)

    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    mock_inspection = MagicMock()
    monkeypatch.setattr(win, "_show_audio_inspection_dialog", mock_inspection)

    mock_restore = MagicMock(wraps=win._restore_from_tray)
    monkeypatch.setattr(win, "_restore_from_tray", mock_restore)

    # 1. Trigger silence_alert
    win._handle_silence_timed_out_to_tray("5m", RecordSourceMode.HYBRID_DUAL)
    assert win._last_tray_message_type == "silence_alert"

    # Click 1 on silence_alert
    win._on_tray_message_clicked()
    assert mock_restore.call_count == 1
    assert mock_inspection.call_count == 1
    assert win._last_tray_message_type is None

    # Click 2 on the same balloon (stale click event)
    win._on_tray_message_clicked()
    assert mock_restore.call_count == 2
    assert mock_inspection.call_count == 1  # MUST REMAIN 1, NOT RE-TRIGGERED!

    # Click 3
    win._on_tray_message_clicked()
    assert mock_restore.call_count == 3
    assert mock_inspection.call_count == 1  # STILL 1

    # 2. Trigger minimized
    save_user_settings({"minimize_to_tray_on_close": True})
    ev = QCloseEvent()
    win.closeEvent(ev)
    assert win._last_tray_message_type == "minimized"

    # Click 1 on minimized
    win._on_tray_message_clicked()
    assert mock_restore.call_count == 4
    assert mock_inspection.call_count == 1  # MUST NOT INCREASE

    # Click 2 on minimized
    win._on_tray_message_clicked()
    assert mock_restore.call_count == 5
    assert mock_inspection.call_count == 1  # MUST NOT INCREASE

    win._force_quit = True
    win.close()
    win.deleteLater()


def test_adv_tray_message_overwrite_race_conditions(qapp, isolated_settings, monkeypatch):
    """
    Test rapid overwrites before user click:
    - Case A: 'minimized' posted, but immediately superseded by 'silence_alert' before click.
      Clicking must inspect with silence_alert mode.
    - Case B: 'silence_alert' posted, but user immediately closes window (minimized) before click.
      Clicking the new minimized notification must NOT open inspection dialog.
    """
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    save_user_settings({"minimize_to_tray_on_close": True})

    win = SmartDictaphoneWindow()
    win.show()
    qapp.processEvents()

    mock_inspection = MagicMock()
    monkeypatch.setattr(win, "_show_audio_inspection_dialog", mock_inspection)

    # --- Case A: minimized -> immediately silence_alert -> click ---
    ev1 = QCloseEvent()
    win.closeEvent(ev1)
    assert win._last_tray_message_type == "minimized"

    # Before click arrives, silence alert fires!
    win._handle_silence_timed_out_to_tray("10m", RecordSourceMode.SYSTEM_ONLY)
    assert win._last_tray_message_type == "silence_alert"

    # Now user clicks
    win._on_tray_message_clicked()
    mock_inspection.assert_called_once_with(RecordSourceMode.SYSTEM_ONLY)
    assert win._last_tray_message_type is None

    # --- Case B: silence_alert -> immediately minimized -> click ---
    mock_inspection.reset_mock()
    win._handle_silence_timed_out_to_tray("15m", RecordSourceMode.MIC_ONLY)
    assert win._last_tray_message_type == "silence_alert"

    # Before user clicks silence alert, user triggers close (minimized to tray)
    ev2 = QCloseEvent()
    win.closeEvent(ev2)
    assert win._last_tray_message_type == "minimized"

    # Now user clicks the minimized notification
    win._on_tray_message_clicked()
    mock_inspection.assert_not_called()
    assert win._last_tray_message_type is None

    win._force_quit = True
    win.close()
    win.deleteLater()


def test_adv_tray_restore_from_minimized_and_hidden_states(qapp, isolated_settings, monkeypatch):
    """
    Verify that _restore_from_tray properly un-minimizes (showNormal) when minimized,
    and shows (show) when hidden.
    """
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)

    win = SmartDictaphoneWindow()

    # Sub-case 1: Hidden state (minimize to tray on close)
    win.hide()
    assert win.isHidden() is True
    win._restore_from_tray()
    qapp.processEvents()
    assert win.isVisible() is True

    # Sub-case 2: Minimized state (showMinimized)
    win.showMinimized()
    qapp.processEvents()
    assert win.isMinimized() is True
    win._restore_from_tray()
    qapp.processEvents()
    assert win.isMinimized() is False
    assert win.isVisible() is True

    win._force_quit = True
    win.close()
    win.deleteLater()


# =============================================================================
# 2. DYNAMIC AUTO-SCROLL STATE TRANSITIONS & CHECKED PRESERVATION
# =============================================================================

def test_adv_autoscroll_rapid_alternation_stress(qapp, isolated_settings):
    """
    Rapidly alternate combo_preview_order 200 times between:
    - 'newest_first' (index 0) -> chk_auto_scroll.isEnabled() MUST be False
    - 'chronological' (index 1) -> chk_auto_scroll.isEnabled() MUST be True
    Verify no desynchronization occurs under rapid hammering.
    """
    tab = AppearanceTab(parent=None, settings=isolated_settings)

    for i in range(200):
        if i % 2 == 0:
            # Switch to chronological
            tab.combo_preview_order.setCurrentIndex(1)
            assert tab.combo_preview_order.currentData() == "chronological"
            assert tab.chk_auto_scroll.isEnabled() is True, f"Cycle {i}: Must be enabled for chronological"
        else:
            # Switch to newest_first
            tab.combo_preview_order.setCurrentIndex(0)
            assert tab.combo_preview_order.currentData() == "newest_first"
            assert tab.chk_auto_scroll.isEnabled() is False, f"Cycle {i}: Must be disabled for newest_first"

    tab.deleteLater()


def test_adv_autoscroll_checked_state_preservation(qapp, isolated_settings):
    """
    Verify that toggling combo_preview_order NEVER modifies or corrupts
    the user's underlying isChecked() preference:
    - User checked: stays checked across transitions and saves as True.
    - User unchecked: stays unchecked across transitions and saves as False.
    """
    tab = AppearanceTab(parent=None, settings=isolated_settings)

    # --- Scenario A: User preference is True (checked) ---
    tab.combo_preview_order.setCurrentIndex(1)  # chronological -> enabled
    tab.chk_auto_scroll.setChecked(True)
    assert tab.chk_auto_scroll.isEnabled() is True
    assert tab.chk_auto_scroll.isChecked() is True

    # Switch to newest_first -> disabled, but isChecked() must remain True!
    tab.combo_preview_order.setCurrentIndex(0)
    assert tab.chk_auto_scroll.isEnabled() is False
    assert tab.chk_auto_scroll.isChecked() is True
    assert tab.get_settings()["auto_scroll_chronological"] is True

    # Switch back to chronological -> enabled, isChecked() must still be True!
    tab.combo_preview_order.setCurrentIndex(1)
    assert tab.chk_auto_scroll.isEnabled() is True
    assert tab.chk_auto_scroll.isChecked() is True
    assert tab.get_settings()["auto_scroll_chronological"] is True

    # --- Scenario B: User preference is False (unchecked) ---
    tab.chk_auto_scroll.setChecked(False)
    assert tab.chk_auto_scroll.isEnabled() is True
    assert tab.chk_auto_scroll.isChecked() is False

    # Switch to newest_first -> disabled, but isChecked() must remain False!
    tab.combo_preview_order.setCurrentIndex(0)
    assert tab.chk_auto_scroll.isEnabled() is False
    assert tab.chk_auto_scroll.isChecked() is False
    assert tab.get_settings()["auto_scroll_chronological"] is False

    # Switch back to chronological -> enabled, isChecked() must still be False!
    tab.combo_preview_order.setCurrentIndex(1)
    assert tab.chk_auto_scroll.isEnabled() is True
    assert tab.chk_auto_scroll.isChecked() is False
    assert tab.get_settings()["auto_scroll_chronological"] is False

    tab.deleteLater()


def test_adv_autoscroll_reset_to_defaults_synchronization(qapp, isolated_settings):
    """
    Verify that reset_to_defaults():
    - Synchronizes preview_order to 'newest_first'
    - Synchronizes auto_scroll_chronological to True
    - Synchronizes chk_auto_scroll.isEnabled() to False (strictly matching newest_first)
    Regardless of prior state (even if prior state was chronological & unchecked).
    """
    tab = AppearanceTab(parent=None, settings=isolated_settings)

    # Dirty the state: chronological, unchecked (enabled=True, checked=False)
    tab.combo_preview_order.setCurrentIndex(1)
    tab.chk_auto_scroll.setChecked(False)
    assert tab.chk_auto_scroll.isEnabled() is True
    assert tab.chk_auto_scroll.isChecked() is False

    # Reset
    tab.reset_to_defaults()
    assert tab.combo_preview_order.currentData() == "newest_first"
    assert tab.chk_auto_scroll.isEnabled() is False
    assert tab.chk_auto_scroll.isChecked() is True

    st = tab.get_settings()
    assert st["preview_order"] == "newest_first"
    assert st["auto_scroll_chronological"] is True

    # Also test reset when already on newest_first but unchecked
    tab.chk_auto_scroll.setChecked(False)
    assert tab.chk_auto_scroll.isChecked() is False
    tab.reset_to_defaults()
    assert tab.combo_preview_order.currentData() == "newest_first"
    assert tab.chk_auto_scroll.isEnabled() is False
    assert tab.chk_auto_scroll.isChecked() is True

    tab.deleteLater()


def test_adv_settings_dialog_restore_defaults_synchronization(qapp, isolated_settings, monkeypatch):
    """
    Verify full dialog integration: SettingsDialog._restore_defaults()
    properly synchronizes appearance_tab and the dialog's alias attributes.
    """
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    dlg = SettingsDialog()

    # Dirty the state to chronological + unchecked
    dlg.combo_preview_order.setCurrentIndex(dlg.combo_preview_order.findData("chronological"))
    dlg.chk_auto_scroll.setChecked(False)
    assert dlg.chk_auto_scroll.isEnabled() is True
    assert dlg.chk_auto_scroll.isChecked() is False

    # Invoke _restore_defaults
    dlg._restore_defaults()

    # Check dialog aliases and appearance tab
    assert dlg.combo_preview_order.currentData() == "newest_first"
    assert dlg.chk_auto_scroll.isEnabled() is False
    assert dlg.chk_auto_scroll.isChecked() is True

    assert dlg.appearance_tab.combo_preview_order.currentData() == "newest_first"
    assert dlg.appearance_tab.chk_auto_scroll.isEnabled() is False
    assert dlg.appearance_tab.chk_auto_scroll.isChecked() is True

    dlg.deleteLater()


def test_adv_autoscroll_edge_case_corrupt_or_empty_settings(qapp):
    """
    Test edge cases in AppearanceTab.load_settings:
    1. Empty dict {} -> safe defaults (newest_first, disabled, checked=True)
    2. None -> safe defaults
    3. Unknown/corrupt preview_order string -> does not crash, fallback safe
    """
    tab = AppearanceTab(parent=None, settings={})

    # Case 1: Empty settings
    tab.load_settings({})
    assert tab.combo_preview_order.currentData() == "newest_first"
    assert tab.chk_auto_scroll.isEnabled() is False
    assert tab.chk_auto_scroll.isChecked() is True

    # Case 2: None
    tab.load_settings(None)
    assert tab.combo_preview_order.currentData() == "newest_first"
    assert tab.chk_auto_scroll.isEnabled() is False
    assert tab.chk_auto_scroll.isChecked() is True

    # Case 3: Corrupt preview_order value
    tab.load_settings({"preview_order": "completely_invalid_order_value_12345", "auto_scroll_chronological": False})
    # Must not crash, currentData is not 'chronological', so auto-scroll must be disabled
    assert tab.chk_auto_scroll.isEnabled() is False
    assert tab.chk_auto_scroll.isChecked() is False

    tab.deleteLater()
