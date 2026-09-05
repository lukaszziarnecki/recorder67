# -*- coding: utf-8 -*-
"""
Testy obciążeniowe i wydajnościowe silnika motywów oraz zasobów czcionek.
Weryfikacja braku wycieków pamięci przy szybkim przełączaniu motywów,
odporności na skrajne wartości rozmiaru czcionki i błędne uchwyty HWND paska tytułu.
"""

import os
import sys

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath("."))

# Ensure headless Qt environment
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import gc
import time
import tracemalloc
from typing import List, Dict, Any
import pytest

try:
    from PySide6.QtCore import Qt, qInstallMessageHandler, QtMsgType
    from PySide6.QtWidgets import (
        QApplication, QWidget, QPushButton, QTextEdit,
        QGroupBox, QComboBox, QSlider, QProgressBar, QLabel,
        QVBoxLayout, QTabWidget, QListWidget
    )
except ImportError:
    from PyQt6.QtCore import Qt, qInstallMessageHandler, QtMsgType
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QPushButton, QTextEdit,
        QGroupBox, QComboBox, QSlider, QProgressBar, QLabel,
        QVBoxLayout, QTabWidget, QListWidget
    )

import psutil
from recorder.ui.theme import (
    THEMES,
    DEFAULT_THEME_ID,
    get_theme,
    get_available_themes,
    generate_theme_qss,
    apply_theme,
    set_window_titlebar_theme,
    get_speaker_colors,
    get_theme_font,
    register_bundled_fonts
)


class QtLogCatcher:
    """Catches and records any Qt internal warnings or parser errors."""
    def __init__(self):
        self.messages = []
        self._old_handler = None

    def __enter__(self):
        self.messages.clear()
        def handler(msg_type, context, message):
            self.messages.append((msg_type, message))
        self._old_handler = qInstallMessageHandler(handler)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        qInstallMessageHandler(self._old_handler)


def get_process_memory_mb() -> float:
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024 * 1024)


def create_sample_ui(app: QApplication) -> QWidget:
    window = QWidget()
    layout = QVBoxLayout(window)

    label_stop = QLabel("Stopped")
    label_stop.setObjectName("StatusStopped")
    layout.addWidget(label_stop)

    label_speech = QLabel("Speech")
    label_speech.setObjectName("StatusSpeech")
    layout.addWidget(label_speech)

    btn_start = QPushButton("Start")
    btn_start.setObjectName("BtnStart")
    layout.addWidget(btn_start)

    btn_stop = QPushButton("Stop")
    btn_stop.setObjectName("BtnStop")
    layout.addWidget(btn_stop)

    group = QGroupBox("Settings")
    layout.addWidget(group)

    combo = QComboBox()
    combo.addItems(["Option A", "Option B", "Option C"])
    layout.addWidget(combo)

    slider = QSlider(Qt.Orientation.Horizontal)
    layout.addWidget(slider)

    progress = QProgressBar()
    progress.setValue(50)
    layout.addWidget(progress)

    text_edit = QTextEdit()
    text_edit.setPlainText("Sample transcription line 1\nSample transcription line 2")
    layout.addWidget(text_edit)

    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Tab 1")
    tabs.addTab(QWidget(), "Tab 2")
    layout.addWidget(tabs)

    list_w = QListWidget()
    list_w.addItems(["Item 1", "Item 2", "Item 3"])
    layout.addWidget(list_w)

    window.show()
    app.processEvents()
    return window


def run_benchmark_theme_switching(app: QApplication, window: QWidget, cycles: int = 100) -> Dict[str, Any]:
    theme_ids = list(THEMES.keys())
    assert len(theme_ids) == 4

    gc.collect()
    mem_initial_rss = get_process_memory_mb()
    tracemalloc.start()

    switch_times = []
    stylesheet_lengths = {th: [] for th in theme_ids}
    memory_checkpoints = []

    total_switches = cycles * len(theme_ids)
    t_start = time.perf_counter()

    for cycle in range(1, cycles + 1):
        for th_id in theme_ids:
            t0 = time.perf_counter()
            qss = apply_theme(app, th_id, font_size=13, window=window)
            app.processEvents()
            t1 = time.perf_counter()

            switch_times.append((t1 - t0) * 1000.0)
            stylesheet_lengths[th_id].append(len(qss))
            app_qss_len = len(app.styleSheet())
            assert app_qss_len == len(qss)

        if cycle in (1, 20, 40, 60, 80, 100) or cycle == cycles:
            current_rss = get_process_memory_mb()
            current_tm, peak_tm = tracemalloc.get_traced_memory()
            memory_checkpoints.append({
                "cycle": cycle,
                "switches": cycle * 4,
                "rss_mb": round(current_rss, 3),
                "tracemalloc_current_kb": round(current_tm / 1024, 2),
                "tracemalloc_peak_kb": round(peak_tm / 1024, 2),
            })

    t_end = time.perf_counter()
    total_elapsed_s = t_end - t_start
    tm_current, tm_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    gc.collect()
    mem_final_rss = get_process_memory_mb()

    bloat_detected = False
    bloat_details = {}
    for th_id, lengths in stylesheet_lengths.items():
        min_len, max_len = min(lengths), max(lengths)
        if min_len != max_len:
            bloat_detected = True
        bloat_details[th_id] = {
            "first_len_bytes": lengths[0],
            "last_len_bytes": lengths[-1],
            "min_len_bytes": min_len,
            "max_len_bytes": max_len,
            "is_constant": (min_len == max_len)
        }

    return {
        "cycles": cycles,
        "total_switches": total_switches,
        "total_elapsed_s": round(total_elapsed_s, 4),
        "avg_switch_ms": round(sum(switch_times) / len(switch_times), 3),
        "min_switch_ms": round(min(switch_times), 3),
        "max_switch_ms": round(max(switch_times), 3),
        "median_switch_ms": round(sorted(switch_times)[len(switch_times)//2], 3),
        "throughput_switches_per_sec": round(total_switches / total_elapsed_s, 1),
        "mem_initial_rss_mb": round(mem_initial_rss, 3),
        "mem_final_rss_mb": round(mem_final_rss, 3),
        "mem_delta_rss_mb": round(mem_final_rss - mem_initial_rss, 3),
        "tracemalloc_peak_kb": round(tm_peak / 1024, 2),
        "memory_checkpoints": memory_checkpoints,
        "bloat_detected": bloat_detected,
        "stylesheet_bloat_details": bloat_details,
    }


def run_test_font_size_boundaries(app: QApplication, window: QWidget) -> List[Dict[str, Any]]:
    test_values = [0, 5, 10, 18, 50, -1, None, 14.5, 9999, -999]
    results = []

    for val in test_values:
        test_record = {
            "input_value": val,
            "input_type": type(val).__name__,
            "clamped_expected": max(10, min(24, int(val))) if val is not None else 13,
            "exception": None,
            "qss_generated": False,
            "qss_length": 0,
            "brace_balance": False,
            "open_braces": 0,
            "close_braces": 0,
            "contains_clamped_font_size": False,
            "qt_parse_errors": [],
            "applied_successfully": False
        }

        try:
            if val is None:
                qss = generate_theme_qss("classic_dark")
                expected_font = 13
            else:
                qss = generate_theme_qss("classic_dark", font_size=val)
                expected_font = max(10, min(24, int(val)))

            test_record["qss_generated"] = True
            test_record["qss_length"] = len(qss)

            open_braces = qss.count("{")
            close_braces = qss.count("}")
            test_record["brace_balance"] = (open_braces == close_braces)
            test_record["open_braces"] = open_braces
            test_record["close_braces"] = close_braces

            expected_rule = f"font-size: {expected_font}px;"
            test_record["contains_clamped_font_size"] = (expected_rule in qss)

            with QtLogCatcher() as catcher:
                window.setStyleSheet(qss)
                app.processEvents()
                parse_errors = [m for t, m in catcher.messages if "Could not parse" in m or "stylesheet" in m.lower()]
                test_record["qt_parse_errors"] = parse_errors
                test_record["applied_successfully"] = (len(parse_errors) == 0)

        except Exception as e:
            test_record["exception"] = f"{type(e).__name__}: {str(e)}"

        results.append(test_record)

    return results


class MockFailingWinIdObject:
    def winId(self):
        raise RuntimeError("Simulated COM/Window handle query failure")


def run_test_hwnd_safety(window: QWidget) -> List[Dict[str, Any]]:
    test_cases = [
        ("Zero (0)", 0),
        ("Negative One (-1)", -1),
        ("None", None),
        ("Extreme Large Int (99999999)", 99999999),
        ("Invalid Object: object()", object()),
        ("Invalid Object: str ('not_a_handle')", "not_a_handle"),
        ("Invalid Object: list ([1, 2, 3])", [1, 2, 3]),
        ("Invalid Object: dict ({'hwnd': 1234})", {"hwnd": 1234}),
        ("Invalid Object: lambda function", lambda: None),
        ("Extreme: INT32_MAX (2147483647)", 2147483647),
        ("Extreme: INT64_MAX (9223372036854775807)", 9223372036854775807),
        ("Extreme: INT32_MIN (-2147483648)", -2147483648),
        ("Object with raising winId()", MockFailingWinIdObject()),
        ("Valid QWidget winId()", int(window.winId()) if hasattr(window, "winId") else 1),
    ]

    results = []
    for name, hwnd_input in test_cases:
        record = {
            "case_name": name,
            "input_val": repr(hwnd_input),
            "returned_value": None,
            "exception_raised": None,
            "is_safe": False
        }
        for is_dark in (True, False):
            try:
                ret = set_window_titlebar_theme(hwnd_input, is_dark=is_dark)
                record["returned_value"] = ret
                record["is_safe"] = True
            except Exception as e:
                record["exception_raised"] = f"{type(e).__name__}: {str(e)}"
                record["is_safe"] = False
                break
            except BaseException as be:
                record["exception_raised"] = f"BaseException {type(be).__name__}: {str(be)}"
                record["is_safe"] = False
                break
        results.append(record)
    return results


# Pytest Test Functions

@pytest.fixture(scope="module")
def qt_environment():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    window = create_sample_ui(app)
    yield app, window
    window.close()


def test_theme_switching_cycles_and_zero_bloat(qt_environment):
    app, window = qt_environment
    res = run_benchmark_theme_switching(app, window, cycles=10)
    assert not res["bloat_detected"], "Stylesheet bloat detected across theme switching"
    assert res["mem_delta_rss_mb"] < 25.0, f"Excessive memory growth: {res['mem_delta_rss_mb']} MB"
    for th_id, dt in res["stylesheet_bloat_details"].items():
        assert dt["is_constant"], f"Stylesheet length varied for theme {th_id}"


@pytest.mark.parametrize("font_val,expected_clamped", [
    (0, 10),
    (5, 10),
    (10, 10),
    (18, 18),
    (50, 24),
    (-1, 10),
    (None, 13),
    (14.5, 14),
    (9999, 24),
    (-999, 10),
])
def test_font_size_boundaries_and_qss_resilience(qt_environment, font_val, expected_clamped):
    app, window = qt_environment
    if font_val is None:
        qss = generate_theme_qss("classic_dark")
    else:
        qss = generate_theme_qss("classic_dark", font_size=font_val)

    assert qss.count("{") == qss.count("}"), "Mismatched QSS curly braces"
    assert f"font-size: {expected_clamped}px;" in qss, f"Expected clamped font-size: {expected_clamped}px;"

    with QtLogCatcher() as catcher:
        window.setStyleSheet(qss)
        app.processEvents()
        parse_errors = [m for t, m in catcher.messages if "Could not parse" in m or "stylesheet" in m.lower()]
        assert len(parse_errors) == 0, f"Qt CSS parser error: {parse_errors}"


@pytest.mark.parametrize("case_name,hwnd_input", [
    ("zero", 0),
    ("negative_one", -1),
    ("none", None),
    ("extreme_large", 99999999),
    ("invalid_object", object()),
    ("invalid_str", "not_a_handle"),
    ("invalid_list", [1, 2, 3]),
    ("invalid_dict", {"hwnd": 1234}),
    ("int32_max", 2147483647),
    ("int64_max", 9223372036854775807),
    ("int32_min", -2147483648),
    ("failing_winId", MockFailingWinIdObject()),
])
def test_hwnd_exception_safety(case_name, hwnd_input):
    for is_dark in (True, False):
        try:
            ret = set_window_titlebar_theme(hwnd_input, is_dark=is_dark)
            assert isinstance(ret, bool)
            assert ret is False
        except Exception as e:
            pytest.fail(f"Uncaught exception for HWND {case_name}: {type(e).__name__}: {e}")
        except BaseException as be:
            pytest.fail(f"Uncaught BaseException for HWND {case_name}: {type(be).__name__}: {be}")


def test_speaker_colors_o1_benchmark():
    theme_ids = list(THEMES.keys())
    lookups = 20000
    t0 = time.perf_counter()
    for i in range(lookups):
        th_id = theme_ids[i % 4]
        colors = get_speaker_colors(th_id)
        _ = colors["mic"]
        _ = colors["system"]
    t1 = time.perf_counter()
    ns_per = ((t1 - t0) / lookups) * 1e9
    assert ns_per < 50000.0, f"Speaker color lookup too slow: {ns_per} ns"


if __name__ == "__main__":
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    window = create_sample_ui(app)
    t1 = run_benchmark_theme_switching(app, window, cycles=100)
    t2 = run_test_font_size_boundaries(app, window)
    t3 = run_test_hwnd_safety(window)
    print("Standalone 100-cycle stress test completed successfully.")
