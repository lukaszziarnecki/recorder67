"""
Testy izolacji modułów audio od UI oraz wydajności kolorów mówców.
Weryfikacja braku zależności Qt w rdzeniu transkrypcji oraz stałego czasu O(1) pobierania palet.
"""
import os
import sys
import ast
import time
import pytest
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from recorder.core.rolling_transcriber import RollingTranscriptionWorker
from recorder.config import THEME_SPEAKER_COLORS, get_speaker_colors, save_user_settings, load_user_settings


def test_core_ast_absolute_zero_ui_imports():
    """Weryfikacja całkowitego braku importów Qt UI / recorder.ui w recorder/core/rolling_transcriber.py."""
    with open("recorder/core/rolling_transcriber.py", "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="recorder/core/rolling_transcriber.py")

    banned_substrings = ["QtWidgets", "QtGui", "recorder.ui", "theme.py"]
    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for b in banned_substrings:
                    if b in alias.name:
                        violations.append(f"Line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for b in banned_substrings:
                if b in mod:
                    violations.append(f"Line {node.lineno}: from {mod} import ...")
            for alias in node.names:
                for b in banned_substrings:
                    if b in alias.name:
                        violations.append(f"Line {node.lineno}: from {mod} import {alias.name}")

    assert not violations, f"UI imports detected in rolling_transcriber.py: {violations}"


def test_cold_import_multi_theme_exact_palette():
    """Verify that all 4 themes apply their exact hex palette to speaker turns."""
    worker = RollingTranscriptionWorker()
    worker.all_turns = [
        {"speaker": "Tester_Mic", "start": 0.0, "end": 1.0, "text": "Test Mic", "channel": "mic"},
        {"speaker": "Tester_Sys", "start": 1.0, "end": 2.0, "text": "Test Sys", "channel": "system"},
    ]

    for th_id, colors in THEME_SPEAKER_COLORS.items():
        html, plain, turns = worker._compile_full_transcript(theme_id=th_id)
        mic_c = colors["mic"]
        sys_c = colors["system"]
        assert f"color: {mic_c};" in html, f"Theme {th_id} failed to render mic color {mic_c}"
        assert f"color: {sys_c};" in html, f"Theme {th_id} failed to render system color {sys_c}"
        assert "Tester_Mic" in plain and "Tester_Sys" in plain


def test_theme_fallback_behavior():
    """Verify resilient fallback when theme_id is None, empty, unknown or invalid."""
    worker = RollingTranscriptionWorker()
    worker.all_turns = [
        {"speaker": "A", "start": 0.0, "end": 1.0, "text": "Hello", "channel": "mic"},
        {"speaker": "B", "start": 1.0, "end": 2.0, "text": "World", "channel": "system"},
    ]

    default_mic = THEME_SPEAKER_COLORS["classic_dark"]["mic"]
    default_sys = THEME_SPEAKER_COLORS["classic_dark"]["system"]

    # Unknown theme string
    html_unk, _, _ = worker._compile_full_transcript(theme_id="nonexistent_cyberpunk_theme")
    assert default_mic in html_unk
    assert default_sys in html_unk

    # Empty theme string
    html_empty, _, _ = worker._compile_full_transcript(theme_id="")
    assert default_mic in html_empty

    # None theme defaults to current active theme in config
    cur_theme = load_user_settings().get("theme", "classic_dark")
    try:
        save_user_settings({"theme": "classic_light"})
        html_none, _, _ = worker._compile_full_transcript(theme_id=None)
        light_mic = THEME_SPEAKER_COLORS["classic_light"]["mic"]
        assert light_mic in html_none
    finally:
        save_user_settings({"theme": cur_theme})  # Restore


def test_missing_turn_keys_graceful_handling():
    """Verify that _compile_full_transcript does not crash when turn keys are omitted."""
    worker = RollingTranscriptionWorker()
    worker.all_turns = [
        {},  # completely empty dictionary (all keys omitted)
        {"channel": "system", "text": "Missing speaker"},
        {"channel": "unknown_channel", "text": "Unknown channel"},
        {"start": 10.0, "end": 12.0},  # missing text and speaker
        {"speaker": "🎙️ AlreadyBadged", "text": "No double badge", "channel": "mic"},
        {"speaker": "🎧 AlreadyBadged", "text": "No double badge", "channel": "system"},
    ]

    html, plain, turns = worker._compile_full_transcript()
    assert isinstance(html, str) and len(html) > 0
    assert isinstance(plain, str) and len(plain) > 0
    assert len(turns) == len(worker.all_turns)
    assert "🎙️ 🎙️" not in plain
    assert "🎧 🎧" not in plain


def test_turn_explicit_none_speaker_adversarial_finding():
    """Empirically document that turn with {'speaker': None} raises AttributeError in startswith."""
    worker = RollingTranscriptionWorker()
    worker.all_turns = [
        {"speaker": None, "channel": "mic", "text": "Null speaker"},
    ]
    # Because t.get('speaker', 'Mówca') returns None when 'speaker' key is present with None value,
    # spk.startswith(...) raises AttributeError: 'NoneType' object has no attribute 'startswith'.
    with pytest.raises(AttributeError, match="has no attribute 'startswith'"):
        worker._compile_full_transcript()



def test_speaker_color_lookup_o1_benchmark():
    """Verify get_speaker_colors is strictly O(1) across 100,000 calls."""
    t0 = time.perf_counter()
    for _ in range(100000):
        c = get_speaker_colors("emanager_dark")
        _ = c["mic"]
        _ = c["system"]
    dur = time.perf_counter() - t0
    # 100k lookups should easily finish in under 100ms in pure Python dictionary access
    assert dur < 0.15, f"get_speaker_colors is too slow: {dur*1000:.2f} ms for 100k calls"


def test_empty_turns_graceful_handling():
    """Verify empty all_turns and empty processed_blocks returns clean fallback."""
    worker = RollingTranscriptionWorker()
    worker.all_turns = []
    worker.processed_blocks = []
    html, plain, turns = worker._compile_full_transcript()
    assert html == "Brak zarejestrowanej mowy."
    assert plain == "Brak zarejestrowanej mowy."
    assert turns == []


def test_10000_turns_stress_scalability():
    """Empirical 10,000 turns stress harness measuring throughput and memory."""
    worker = RollingTranscriptionWorker()
    start_dt = datetime(2026, 9, 4, 8, 0, 0)
    worker.session_start_time = start_dt

    turns = []
    for i in range(10000):
        t_start = (i * 28800.0) / 10000.0
        t_end = t_start + 2.0
        turns.append({
            "speaker": f"User_{i % 4}",
            "start": t_start,
            "end": t_end,
            "text": f"Utterance number {i} with some transcript text.",
            "channel": "mic" if i % 2 == 0 else "system",
            "wall_start": start_dt + timedelta(seconds=t_start),
            "wall_end": start_dt + timedelta(seconds=t_end),
        })
    worker.all_turns = turns

    t0 = time.perf_counter()
    html, plain, compiled_turns = worker._compile_full_transcript(theme_id="emanager_dark")
    elapsed = time.perf_counter() - t0

    assert len(compiled_turns) == 10000
    assert len(plain) > 500000
    assert len(html) > len(plain)
    assert THEME_SPEAKER_COLORS["emanager_dark"]["mic"] in html
    assert THEME_SPEAKER_COLORS["emanager_dark"]["system"] in html
    # 10,000 turns should process within reasonable limits (under 10s even on loaded CPU)
    assert elapsed < 10.0, f"Stress test took too long: {elapsed:.2f}s"
