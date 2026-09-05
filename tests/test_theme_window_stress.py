"""
Testy obciążeniowe stanów okna i dynamicznych reguł QSS.
Zakres:
1. Dynamiczne przejścia stanów wskaźnika chmury (_set_cloud_status)
2. Permutacje etykiet trybów źródła dźwięku (_update_source_mode_labels)
3. Zmiany stanów przycisków wyciszenia mikrofonu i systemu (_update_mute_btn_state)
4. Przejścia stanów VAD i odporność na szybkie przełączanie (_set_vad_state)
5. Selektory dynamicznych stylów QSS dla wszystkich 4 motywów
6. Spójność układu widżetów paska źródeł dźwięku
7. Weryfikacja braku wycieków pamięci przy 10 000 szybkich operacji UI
"""

import os
import sys
import time
import tracemalloc
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from PySide6.QtWidgets import QApplication, QPushButton, QLabel, QComboBox
    from PySide6.QtCore import Qt
except ImportError:
    from PyQt6.QtWidgets import QApplication, QPushButton, QLabel, QComboBox
    from PyQt6.QtCore import Qt

from recorder.ui.window import SmartDictaphoneWindow
from recorder.ui import theme
from recorder.config import RecordSourceMode


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def window(qapp, monkeypatch):
    """Instantiates SmartDictaphoneWindow in headless mode with worker stopped."""
    win = SmartDictaphoneWindow()
    yield win
    try:
        win.close()
        win.deleteLater()
    except Exception:
        pass


def test_cloud_status_transitions(window):
    """
    Challenge 1: _set_cloud_status across all valid and invalid states.
    Verifies property assignment, text update, and unpolish/polish cycle.
    """
    valid_states = ["info", "success", "warning", "error", "purple"]
    for st in valid_states:
        msg = f"Status test message for {st}"
        window._set_cloud_status(msg, st)
        assert window.lbl_cloud_status.text() == msg
        assert window.lbl_cloud_status.property("status") == st

    # Edge cases: None, empty, custom status, integer
    window._set_cloud_status("Unknown status", "unknown_custom")
    assert window.lbl_cloud_status.property("status") == "unknown_custom"

    window._set_cloud_status("Default status")
    assert window.lbl_cloud_status.property("status") == "info"

    # Rapid transitions
    for i in range(100):
        st = valid_states[i % len(valid_states)]
        window._set_cloud_status(f"Rapid {i}", st)
    assert window.lbl_cloud_status.property("status") == valid_states[99 % len(valid_states)]


def test_source_mode_labels_permutations(window):
    """
    Challenge 2: _update_source_mode_labels with all 8 permutations of (mic, sys, app).
    Verifies that 'active' property is correctly set to string 'true' or 'false'.
    """
    for mic in (True, False):
        for sys_act in (True, False):
            for app_act in (True, False):
                window._update_source_mode_labels(mic, sys_act, app_act)
                assert window.lbl_mic_input.property("active") == ("true" if mic else "false"), (
                    f"lbl_mic_input active mismatch for ({mic}, {sys_act}, {app_act})"
                )
                assert window.lbl_sys_input.property("active") == ("true" if sys_act else "false"), (
                    f"lbl_sys_input active mismatch for ({mic}, {sys_act}, {app_act})"
                )
                assert window.lbl_app_input.property("active") == ("true" if app_act else "false"), (
                    f"lbl_app_input active mismatch for ({mic}, {sys_act}, {app_act})"
                )

    # Integracja z RecordSourceMode — przed każdą zmianą resetujemy do innego trybu,
    # aby Qt zawsze emitował sygnał currentIndexChanged (Qt pomija sygnał gdy indeks
    # nie zmienia się, co powodowało niestabilność przy izolowanym uruchamianiu testu).
    hybrid_idx = window.combo_source_mode.findData(RecordSourceMode.HYBRID_DUAL)

    window.combo_source_mode.setCurrentIndex(hybrid_idx)
    window.combo_source_mode.setCurrentIndex(window.combo_source_mode.findData(RecordSourceMode.MIC_ONLY))
    assert window.lbl_mic_input.property("active") == "true"
    assert window.lbl_sys_input.property("active") == "false"
    assert window.lbl_app_input.property("active") == "false"

    window.combo_source_mode.setCurrentIndex(hybrid_idx)
    window.combo_source_mode.setCurrentIndex(window.combo_source_mode.findData(RecordSourceMode.SYSTEM_ONLY))
    assert window.lbl_mic_input.property("active") == "false"
    assert window.lbl_sys_input.property("active") == "true"
    assert window.lbl_app_input.property("active") == "true"

    window.combo_source_mode.setCurrentIndex(window.combo_source_mode.findData(RecordSourceMode.MIC_ONLY))
    window.combo_source_mode.setCurrentIndex(hybrid_idx)
    assert window.lbl_mic_input.property("active") == "true"
    assert window.lbl_sys_input.property("active") == "true"
    assert window.lbl_app_input.property("active") == "true"



def test_mute_btn_state_transitions(window):
    """
    Challenge 3: _update_mute_btn_state for mic and sys buttons.
    Verifies 'muted' property and style update.
    """
    for btn, name in [(window.btn_mute_mic, "BtnMuteMic"), (window.btn_mute_sys, "BtnMuteSys")]:
        assert btn.objectName() == name
        window._update_mute_btn_state(btn, True)
        assert btn.property("muted") == "true"

        window._update_mute_btn_state(btn, False)
        assert btn.property("muted") == "false"

        # Rapid toggles
        for i in range(200):
            is_muted = (i % 2 == 0)
            window._update_mute_btn_state(btn, is_muted)
            assert btn.property("muted") == ("true" if is_muted else "false")


def test_vad_state_rapid_transitions_and_skipping(window):
    """
    Challenge 4: _set_vad_state rapid transitions and skipping unpolish/polish on unchanged state.
    """
    polish_call_count = 0
    orig_polish = window.lbl_vad_detail.style().polish

    def spy_polish(widget):
        nonlocal polish_call_count
        if widget is window.lbl_vad_detail:
            polish_call_count += 1
        return orig_polish(widget)

    window.lbl_vad_detail.style().polish = spy_polish

    # Initial state transition
    window._last_vad_state = None
    polish_call_count = 0

    # Transition to speech
    window._set_vad_state("speech")
    assert window.lbl_vad_detail.property("vad_state") == "speech"
    assert polish_call_count == 1

    # Call 'speech' 500 times with identical state -> must NOT re-polish
    for _ in range(500):
        window._set_vad_state("speech")
    assert polish_call_count == 1, (
        f"Expected polish to be skipped on unchanged VAD state, but polish was called {polish_call_count} times!"
    )

    # Transition to paused
    window._set_vad_state("paused")
    assert window.lbl_vad_detail.property("vad_state") == "paused"
    assert polish_call_count == 2

    # Call 'paused' 500 times -> must NOT re-polish
    for _ in range(500):
        window._set_vad_state("paused")
    assert polish_call_count == 2

    # Transition to silence
    window._set_vad_state("silence")
    assert window.lbl_vad_detail.property("vad_state") == "silence"
    assert polish_call_count == 3

    # Call 'silence' 500 times -> must NOT re-polish
    for _ in range(500):
        window._set_vad_state("silence")
    assert polish_call_count == 3


def test_dynamic_qss_rules_all_themes():
    """
    Challenge 5: Verify that generate_theme_qss across all 4 themes contains
    all required dynamic property selectors.
    """
    required_selectors = [
        'QLabel#CloudStatus[status="info"]',
        'QLabel#CloudStatus[status="success"]',
        'QLabel#CloudStatus[status="warning"]',
        'QLabel#CloudStatus[status="error"]',
        'QLabel#CloudStatus[status="purple"]',
        'QLabel#LblMicInput[active="true"]',
        'QLabel#LblSysInput[active="true"]',
        'QLabel#LblAppInput[active="true"]',
        'QPushButton#BtnMuteMic[muted="true"]',
        'QPushButton#BtnMuteSys[muted="true"]',
        'QLabel#VadDetail[vad_state="speech"]',
        'QLabel#VadDetail[vad_state="paused"]',
        'QLabel#VadDetail[vad_state="silence"]',
    ]
    for th_id in ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]:
        qss = theme.generate_theme_qss(th_id)
        for sel in required_selectors:
            assert sel in qss or sel.replace(" ", "") in qss.replace(" ", ""), (
                f"Missing dynamic selector '{sel}' in theme '{th_id}'"
            )


def test_sources_layout_duplicate_widget_adversarial_check(window):
    """
    Challenge 6: Check for layout corruption or duplicate widgets in sources_box / app_row.
    In window.py, lines 527-546 duplicated self.combo_target_apps, self.btn_refresh_apps,
    and app_row.addWidget calls.
    Row 3 in sources_layout MUST only contain 3 widgets:
    (QLabel LblAppInput, QComboBox combo_target_apps, QPushButton btn_refresh_apps).
    If 5 widgets exist, this is an empirical BUG.
    """
    sources_box = window.combo_source_mode.parentWidget()
    sources_layout = sources_box.layout()
    assert sources_layout is not None

    # Find the app_row layout (item 3 in sources_layout)
    app_row_item = sources_layout.itemAt(3)
    assert app_row_item is not None
    app_row = app_row_item.layout()
    assert app_row is not None

    row_widgets = [
        app_row.itemAt(i).widget()
        for i in range(app_row.count())
        if app_row.itemAt(i).widget() is not None
    ]

    refresh_btns = [
        w for w in row_widgets
        if isinstance(w, QPushButton) and "aktywnych program" in w.toolTip()
    ]
    combo_boxes = [w for w in row_widgets if isinstance(w, QComboBox)]

    # Assert no duplicate widgets in app_row
    assert len(refresh_btns) == 1, (
        f"EMPIRICAL BUG DETECTED: Expected 1 refresh app button in app_row, found {len(refresh_btns)} (duplicated)"
    )
    assert len(combo_boxes) == 1, (
        f"EMPIRICAL BUG DETECTED: Expected 1 target apps combo box in app_row, found {len(combo_boxes)} (duplicated)"
    )
    assert len(row_widgets) == 3, (
        f"EMPIRICAL BUG DETECTED: app_row has {len(row_widgets)} widgets instead of 3: {row_widgets}"
    )


def test_memory_and_cpu_stress_10000_operations(window):
    """
    Challenge 7: Stress harness running 10,000 rapid dynamic property operations.
    Monitors memory growth (tracemalloc) and execution time.
    Must not cause memory leak (> 2 MB) or CPU spikes (> 2.5 s).
    """
    tracemalloc.start()
    gc_before = tracemalloc.take_snapshot()

    start_time = time.perf_counter()

    states = ["speech", "silence", "paused"]
    cloud_states = ["info", "success", "warning", "error", "purple"]

    for i in range(10000):
        # 1. VAD state (switching every 10 iterations to simulate speech chunks)
        window._set_vad_state(states[(i // 10) % len(states)])

        # 2. Cloud status (switching every 50 iterations)
        if i % 50 == 0:
            window._set_cloud_status(f"Sync step {i}", cloud_states[(i // 50) % len(cloud_states)])

        # 3. Mute state toggle
        if i % 100 == 0:
            window._update_mute_btn_state(window.btn_mute_mic, (i % 200 == 0))
            window._update_mute_btn_state(window.btn_mute_sys, (i % 200 != 0))

        # 4. Source mode label updates
        if i % 250 == 0:
            window._update_source_mode_labels(
                mic_active=(i % 500 == 0),
                sys_active=(i % 250 == 0),
                app_active=True
            )

    elapsed = time.perf_counter() - start_time
    gc_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    top_stats = gc_after.compare_to(gc_before, 'lineno')
    total_mem_delta_kb = sum(stat.size_diff for stat in top_stats) / 1024.0

    print(f"\\n[Stress Test 10,000 ops] Elapsed: {elapsed:.3f}s, Memory delta: {total_mem_delta_kb:.2f} KB")

    # Verification thresholds:
    assert elapsed < 2.5, f"Performance failure: 10,000 ops took {elapsed:.3f}s (exceeds 2.5s limit)"
    assert total_mem_delta_kb < 2048, f"Memory leak detected: memory grew by {total_mem_delta_kb:.2f} KB (> 2MB)"
