"""
Testy hierarchii układu widżetów okna i dynamicznych właściwości motywów.
Weryfikacja braku osieroconych kontrolek, braku duplikatów w layoutach oraz poprawności stylów.
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
    from PySide6.QtWidgets import QApplication, QWidget, QLayout, QPushButton, QLabel, QComboBox, QGroupBox, QFrame
    from PySide6.QtCore import Qt
except ImportError:
    from PyQt6.QtWidgets import QApplication, QWidget, QLayout, QPushButton, QLabel, QComboBox, QGroupBox, QFrame
    from PyQt6.QtCore import Qt

from recorder.ui.window import SmartDictaphoneWindow
from recorder.ui import theme
from recorder.config import RecordSourceMode


@pytest.fixture
def window(qapp):
    win = SmartDictaphoneWindow()
    yield win
    try:
        win._force_quit = True
        win.close()
        win.deleteLater()
        qapp.processEvents()
    except Exception:
        pass


def test_sources_layout_and_app_row_exact_structure(window):
    """
    Challenge 1: Verify exact layout structure of sources_box and app_row.
    Confirms:
    - sources_layout contains exactly 4 row layouts
    - app_row contains exactly: LblAppInput, combo_target_apps, btn_refresh_apps
    - No duplicate refresh buttons or duplicate comboboxes
    """
    sources_box = window.combo_source_mode.parentWidget()
    assert isinstance(sources_box, QGroupBox)
    sources_layout = sources_box.layout()
    assert sources_layout is not None
    assert sources_layout.count() == 4, f"sources_layout should have 4 rows, found {sources_layout.count()}"

    # Row 0: Mode row
    mode_row = sources_layout.itemAt(0).layout()
    assert mode_row is not None
    mode_widgets = [mode_row.itemAt(i).widget() for i in range(mode_row.count()) if mode_row.itemAt(i).widget()]
    assert any(w.objectName() == "AudioSourceModeLabel" for w in mode_widgets)
    assert window.combo_source_mode in mode_widgets

    # Row 1: Mic row
    mic_row = sources_layout.itemAt(1).layout()
    assert mic_row is not None
    mic_widgets = [mic_row.itemAt(i).widget() for i in range(mic_row.count()) if mic_row.itemAt(i).widget()]
    assert window.lbl_mic_input in mic_widgets
    assert window.combo_devices in mic_widgets
    assert window.btn_refresh_dev in mic_widgets

    # Row 2: System loopback row
    sys_row = sources_layout.itemAt(2).layout()
    assert sys_row is not None
    sys_widgets = [sys_row.itemAt(i).widget() for i in range(sys_row.count()) if sys_row.itemAt(i).widget()]
    assert window.lbl_sys_input in sys_widgets
    assert window.combo_loopback_devices in sys_widgets
    assert window.btn_refresh_loop in sys_widgets

    # Row 3: App row
    app_row = sources_layout.itemAt(3).layout()
    assert app_row is not None
    app_widgets = [app_row.itemAt(i).widget() for i in range(app_row.count()) if app_row.itemAt(i).widget()]
    assert len(app_widgets) == 3, f"Expected exactly 3 widgets in app_row, got {len(app_widgets)}"
    assert app_widgets[0] is window.lbl_app_input
    assert app_widgets[1] is window.combo_target_apps
    assert app_widgets[2] is window.btn_refresh_apps


def test_no_orphan_widgets_in_entire_window(window):
    """
    Challenge 2: Verify that every QWidget attribute on SmartDictaphoneWindow
    is properly parented and belongs to the window's widget tree.
    """
    orphans = []
    for name in dir(window):
        if name.startswith("_"):
            continue
        try:
            val = getattr(window, name)
            if isinstance(val, QWidget) and val is not window:
                if val.window() is not window:
                    orphans.append((name, type(val).__name__, val.objectName()))
        except Exception:
            pass

    assert len(orphans) == 0, f"Found orphan widgets not belonging to window: {orphans}"


def test_no_duplicate_widgets_across_layouts(window):
    """
    Challenge 3: Traverse entire layout tree and verify no widget is added more than once.
    """
    scroll_area = window.centralWidget()
    main_widget = scroll_area.widget()
    main_layout = main_widget.layout()

    widget_occurrences = {}

    def walk_layout(layout, path):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            sub_l = item.layout()
            if w is not None:
                widget_occurrences.setdefault(w, []).append((path, i))
                if w.layout() is not None:
                    walk_layout(w.layout(), f"{path}->{w.objectName() or type(w).__name__}.layout")
            if sub_l is not None:
                walk_layout(sub_l, f"{path}->sub_{type(sub_l).__name__}[{i}]")

    walk_layout(main_layout, "main")

    duplicates = {w: paths for w, paths in widget_occurrences.items() if len(paths) > 1}
    assert len(duplicates) == 0, f"Found duplicate widgets in layout tree: {duplicates}"


def test_no_overlapping_sibling_controls(window):
    """
    Challenge 4: Check for overlapping bounding rectangles between sibling controls
    at various window resolutions (800x600, 1024x768, 1440x900).
    """
    resolutions = [(800, 600), (1024, 768), (1440, 900)]
    for w_val, h_val in resolutions:
        window.resize(w_val, h_val)
        window.show()
        QApplication.processEvents()

        parent_map = {}
        for child in window.findChildren(QWidget):
            if child.isVisible():
                p = child.parentWidget()
                if p:
                    parent_map.setdefault(p, []).append(child)

        overlaps = []
        for p, children in parent_map.items():
            if type(p).__name__ in ("QStackedWidget", "QTabWidget"):
                continue
            vis_children = [c for c in children if c.isVisible() and c.width() > 0 and c.height() > 0]
            for i in range(len(vis_children)):
                for j in range(i + 1, len(vis_children)):
                    c1 = vis_children[i]
                    c2 = vis_children[j]
                    if c1.isAncestorOf(c2) or c2.isAncestorOf(c1):
                        continue
                    r1 = c1.geometry()
                    r2 = c2.geometry()
                    intersection = r1.intersected(r2)
                    # Ignore 1px border overlaps, check for real overlaps > 2px in both dimensions
                    if not intersection.isEmpty() and intersection.width() > 2 and intersection.height() > 2:
                        overlaps.append((
                            p.objectName() or type(p).__name__,
                            c1.objectName() or type(c1).__name__,
                            c2.objectName() or type(c2).__name__,
                            intersection.getRect()
                        ))

        assert len(overlaps) == 0, f"Overlapping controls at {w_val}x{h_val}: {overlaps}"


def test_vad_state_high_frequency_alternation_stress(window):
    """
    Challenge 5: Stress-test rapid alternating VAD state (speech <-> silence)
    where unpolish and polish ARE invoked 3,000 times consecutively.
    Must complete without memory spikes or crashes.
    """
    tracemalloc.start()
    gc_before = tracemalloc.take_snapshot()
    start_time = time.perf_counter()

    for i in range(3000):
        state = "speech" if i % 2 == 0 else "silence"
        window._set_vad_state(state)
        assert window.lbl_vad_detail.property("vad_state") == state

    elapsed = time.perf_counter() - start_time
    gc_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    top_stats = gc_after.compare_to(gc_before, "lineno")
    mem_delta_kb = sum(stat.size_diff for stat in top_stats) / 1024.0

    print(f"\\n[3,000 VAD Alternations] Elapsed: {elapsed:.3f}s, Memory delta: {mem_delta_kb:.2f} KB")
    assert elapsed < 2.0, f"VAD rapid switching took too long: {elapsed:.3f}s"
    assert mem_delta_kb < 1024, f"VAD rapid switching leaked memory: {mem_delta_kb:.2f} KB"


def test_dynamic_properties_persistence_across_all_themes(window):
    """
    Challenge 6: Apply non-default dynamic property states, switch across all 4 themes,
    and verify that dynamic properties are perfectly preserved.
    """
    window._set_cloud_status("Sync Error Occurred", "error")
    window._update_source_mode_labels(mic_active=True, sys_active=False, app_active=True)
    window._update_mute_btn_state(window.btn_mute_mic, True)
    window._update_mute_btn_state(window.btn_mute_sys, False)
    window._set_vad_state("speech")

    all_themes = ["classic_dark", "classic_light", "emanager_dark", "emanager_light", "classic_dark"]
    for th in all_themes:
        theme.apply_theme(window, th)

        # Assert property values survive theme reload
        assert window.lbl_cloud_status.property("status") == "error"
        assert window.lbl_mic_input.property("active") == "true"
        assert window.lbl_sys_input.property("active") == "false"
        assert window.lbl_app_input.property("active") == "true"
        assert window.btn_mute_mic.property("muted") == "true"
        assert window.btn_mute_sys.property("muted") == "false"
        assert window.lbl_vad_detail.property("vad_state") == "speech"


def test_dynamic_property_edge_cases_and_adversarial_inputs(window):
    """
    Challenge 7: Pass edge cases and unusual values to dynamic property methods:
    empty string, 10,000-character string, Unicode / Polish diacritics / emojis,
    HTML tags, and custom/unrecognized statuses.
    """
    edge_cases = [
        ("", "info"),
        ("A" * 10000, "warning"),
        ("🔥 Zażółć gęślą jaźń / 语音识别 / 🎧", "purple"),
        ("<script>alert('xss')</script>", "error"),
        ("<b>HTML Tagged Status</b>", "success"),
        ("Custom unrecognized status text", "non_existent_status_123"),
    ]

    for text, status in edge_cases:
        window._set_cloud_status(text, status)
        assert window.lbl_cloud_status.text() == text
        assert window.lbl_cloud_status.property("status") == status
