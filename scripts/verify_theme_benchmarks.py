"""
Skrypt weryfikacji i benchmarków silnika motywów:
Wydajność O(1) kolorów mówców, współczynniki kontrastu WCAG 2.1 oraz analiza widoczności ikony aplikacji.
"""

import os
import sys
import time
import tracemalloc
from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from recorder.ui.theme import (
    THEMES,
    get_speaker_colors,
    THEME_SPEAKER_COLORS,
    DEFAULT_THEME_ID,
)


def relative_luminance(hex_color: str) -> float:
    """Calculates relative luminance according to WCAG 2.1 definition."""
    hex_clean = hex_color.lstrip("#")
    r, g, b = [int(hex_clean[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
    r_lin = r / 12.92 if r <= 0.04045 else ((r + 0.055) / 1.055) ** 2.4
    g_lin = g / 12.92 if g <= 0.04045 else ((g + 0.055) / 1.055) ** 2.4
    b_lin = b / 12.92 if b <= 0.04045 else ((b + 0.055) / 1.055) ** 2.4
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def rgb_relative_luminance(r: float, g: float, b: float) -> float:
    """Calculates relative luminance for linear/normalized RGB floats [0.0, 1.0]."""
    r_lin = r / 12.92 if r <= 0.04045 else ((r + 0.055) / 1.055) ** 2.4
    g_lin = g / 12.92 if g <= 0.04045 else ((g + 0.055) / 1.055) ** 2.4
    b_lin = b / 12.92 if b <= 0.04045 else ((b + 0.055) / 1.055) ** 2.4
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(c1: str, c2: str) -> float:
    """Calculates WCAG 2.1 contrast ratio between two hex colors."""
    l1 = relative_luminance(c1)
    l2 = relative_luminance(c2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def run_speaker_colors_benchmark(n: int = 500000):
    print("=" * 70)
    print(f"BENCHMARK 1: Speaker Colors O(1) Performance (N={n:,} iterations)")
    print("=" * 70)

    # Baseline 1: Empty loop overhead
    t0_empty = time.perf_counter_ns()
    for _ in range(n):
        pass
    t1_empty = time.perf_counter_ns()
    empty_loop_ns = (t1_empty - t0_empty) / n
    print(f"Baseline Python empty loop overhead: {empty_loop_ns:.2f} ns/iter")

    # Baseline 2: Empty function call overhead
    def _empty(): pass
    t0_fn = time.perf_counter_ns()
    for _ in range(n):
        _empty()
    t1_fn = time.perf_counter_ns()
    fn_call_ns = (t1_fn - t0_fn) / n
    print(f"Baseline Python empty function call overhead: {fn_call_ns:.2f} ns/call")

    # 1. Direct dictionary lookup: THEME_SPEAKER_COLORS[theme_id]
    t0_dict = time.perf_counter_ns()
    for _ in range(n):
        _ = THEME_SPEAKER_COLORS["classic_dark"]
    t1_dict = time.perf_counter_ns()
    dict_total_ns = (t1_dict - t0_dict) / n
    dict_net_ns = dict_total_ns - empty_loop_ns

    # Memory allocation test for direct dictionary lookup
    tracemalloc.start()
    snap1_d = tracemalloc.take_snapshot()
    for _ in range(10000):
        _ = THEME_SPEAKER_COLORS["classic_dark"]
    snap2_d = tracemalloc.take_snapshot()
    cur_d, peak_d = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    alloc_d = sum(s.size_diff for s in snap2_d.compare_to(snap1_d, "lineno") if s.size_diff > 0)

    print("\n[A] Direct Precomputed Dictionary Lookup: THEME_SPEAKER_COLORS['classic_dark']")
    print(f"    - Gross time per lookup: {dict_total_ns:.2f} ns")
    print(f"    - Net lookup time (minus loop): {dict_net_ns:.2f} ns (< 100ns target: {'PASS' if dict_net_ns < 100 else 'FAIL'})")
    print(f"    - Heap memory allocated: {alloc_d} bytes (zero allocation target: {'PASS' if alloc_d == 0 else 'FAIL'})")

    # 2. Functional lookup: get_speaker_colors(theme_id)
    t0_func = time.perf_counter_ns()
    for _ in range(n):
        _ = get_speaker_colors("classic_dark")
    t1_func = time.perf_counter_ns()
    func_total_ns = (t1_func - t0_func) / n
    func_net_ns = func_total_ns - empty_loop_ns

    # Memory allocation test for get_speaker_colors
    tracemalloc.start()
    snap1_f = tracemalloc.take_snapshot()
    for _ in range(10000):
        _ = get_speaker_colors("classic_dark")
    snap2_f = tracemalloc.take_snapshot()
    cur_f, peak_f = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    alloc_f = sum(s.size_diff for s in snap2_f.compare_to(snap1_f, "lineno") if s.size_diff > 0)

    print("\n[B] Current Function Lookup: get_speaker_colors('classic_dark')")
    print(f"    - Gross time per lookup: {func_total_ns:.2f} ns")
    print(f"    - Net lookup time (minus loop): {func_net_ns:.2f} ns (< 100ns target: {'PASS' if func_net_ns < 100 else 'FAIL'})")
    print(f"    - Net memory allocated: {alloc_f} bytes (zero allocation target: {'PASS' if alloc_f == 0 else 'FAIL'})")
    print("    - Finding: get_speaker_colors instantiates a new dict {'mic': ..., 'system': ...} on each call.")
    print("    - Recommendation: Update get_speaker_colors to return THEME_SPEAKER_COLORS[theme_id] for true zero allocation.")


def run_wcag_contrast_benchmark():
    print("\n" + "=" * 70)
    print("BENCHMARK 2: WCAG 2.1 Relative Luminance and Contrast Ratio")
    print("=" * 70)
    targets = [
        ("classic_dark", "#111216", "#4cc9f0", "#a370f7"),
        ("classic_light (pure white)", "#ffffff", "#0369a1", "#7c3aed"),
        ("classic_light (slate white)", "#f8fafc", "#0369a1", "#7c3aed"),
        ("emanager_dark", "#0c0e12", "#ff6b6b", "#38bdf8"),
        ("emanager_light", "#fafafa", "#b91c1c", "#1d4ed8"),
    ]

    all_pass = True
    for theme_id, bg, mic, sys_col in targets:
        l_bg = relative_luminance(bg)
        l_mic = relative_luminance(mic)
        l_sys = relative_luminance(sys_col)
        cr_mic = contrast_ratio(bg, mic)
        cr_sys = contrast_ratio(bg, sys_col)

        pass_mic_normal = cr_mic >= 4.5
        pass_mic_large = cr_mic >= 3.0
        pass_sys_normal = cr_sys >= 4.5
        pass_sys_large = cr_sys >= 3.0

        if not (pass_mic_normal and pass_sys_normal):
            all_pass = False

        print(f"\nTheme: {theme_id} | Background: {bg} (L = {l_bg:.4f})")
        print(f"  - Microphone color {mic}: L = {l_mic:.4f} | Contrast Ratio: {cr_mic:5.2f}:1 | WCAG AA Normal: {'PASS' if pass_mic_normal else 'FAIL'}")
        print(f"  - System audio color {sys_col}: L = {l_sys:.4f} | Contrast Ratio: {cr_sys:5.2f}:1 | WCAG AA Normal: {'PASS' if pass_sys_normal else 'FAIL'}")

    print(f"\nWCAG Contrast Benchmark Verdict: {'ALL PASS (100% compliant with WCAG AA >= 4.5:1)' if all_pass else 'FAIL'}")


def run_icon_contrast_benchmark():
    print("\n" + "=" * 70)
    print("BENCHMARK 3: App Icon Stand & Base Pixels Analysis on #ffffff")
    print("=" * 70)
    icon_path = os.path.join(REPO_ROOT, "recorder", "resources", "app_icon.png")
    assert os.path.exists(icon_path), f"File not found: {icon_path}"

    img = Image.open(icon_path).convert("RGBA")
    w, h = img.size
    print(f"Image loaded: {icon_path} ({w}x{h} px)")

    # Composite over pure white #ffffff
    white_bg = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    composited = Image.alpha_composite(white_bg, img).convert("RGB")

    # 1. Vertical stand leg (y=190, x=120..136)
    print("\n[Cross-Section 1] Vertical Stand Leg at y=190:")
    left_outline_cr = 0.0
    right_outline_cr = 0.0
    inner_core_cr = 0.0

    for x in range(120, 137):
        p = composited.getpixel((x, 190))
        lum = rgb_relative_luminance(*(c / 255.0 for c in p))
        cr = 1.05 / (lum + 0.05)
        if x in (122, 123):
            left_outline_cr = max(left_outline_cr, cr)
        elif x == 128:
            inner_core_cr = cr
        elif x in (132, 133):
            right_outline_cr = max(right_outline_cr, cr)

    print(f"  - Left outline edge (x=122..123): Contrast Ratio = {left_outline_cr:.2f}:1 (>= 3:1 target: {'PASS' if left_outline_cr >= 3.0 else 'FAIL'})")
    print(f"  - Inner silver body (x=128): Contrast Ratio = {inner_core_cr:.2f}:1")
    print(f"  - Right outline edge (x=132..133): Contrast Ratio = {right_outline_cr:.2f}:1 (>= 3:1 target: {'PASS' if right_outline_cr >= 3.0 else 'FAIL'})")

    # 2. Horizontal base cross-section (x=120, y=204..216)
    print("\n[Cross-Section 2] Horizontal Base Vertical Profile at x=120:")
    top_outline_cr = 0.0
    bottom_outline_cr = 0.0
    for y in range(204, 217):
        p = composited.getpixel((120, y))
        lum = rgb_relative_luminance(*(c / 255.0 for c in p))
        cr = 1.05 / (lum + 0.05)
        if y in (204, 205):
            top_outline_cr = max(top_outline_cr, cr)
        elif y in (214, 215):
            bottom_outline_cr = max(bottom_outline_cr, cr)

    print(f"  - Top outline edge (y=204..205): Contrast Ratio = {top_outline_cr:.2f}:1 (>= 3:1 target: {'PASS' if top_outline_cr >= 3.0 else 'FAIL'})")
    print(f"  - Bottom outline edge (y=214..215): Contrast Ratio = {bottom_outline_cr:.2f}:1 (>= 3:1 target: {'PASS' if bottom_outline_cr >= 3.0 else 'FAIL'})")

    # 3. Horizontal base left and right caps
    p_left_cap = composited.getpixel((95, 210))
    lum_left_cap = rgb_relative_luminance(*(c / 255.0 for c in p_left_cap))
    cr_left_cap = 1.05 / (lum_left_cap + 0.05)

    p_right_cap = composited.getpixel((160, 210))
    lum_right_cap = rgb_relative_luminance(*(c / 255.0 for c in p_right_cap))
    cr_right_cap = 1.05 / (lum_right_cap + 0.05)

    print(f"  - Left base end cap (x=95, y=210): Contrast Ratio = {cr_left_cap:.2f}:1 (>= 3:1 target: {'PASS' if cr_left_cap >= 3.0 else 'FAIL'})")
    print(f"  - Right base end cap (x=160, y=210): Contrast Ratio = {cr_right_cap:.2f}:1 (>= 3:1 target: {'PASS' if cr_right_cap >= 3.0 else 'FAIL'})")

    icon_pass = all(cr >= 3.0 for cr in [left_outline_cr, right_outline_cr, top_outline_cr, bottom_outline_cr, cr_left_cap, cr_right_cap])
    print(f"\nIcon Outline Contrast Verdict: {'PASS (Contrast >= 3:1 confirmed on pure white)' if icon_pass else 'FAIL'}")


if __name__ == "__main__":
    run_speaker_colors_benchmark()
    run_wcag_contrast_benchmark()
    run_icon_contrast_benchmark()
