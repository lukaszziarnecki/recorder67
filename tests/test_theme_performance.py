"""
Testy wydajnościowe zamykania okna ustawień, pamięci podręcznej QSS i kart motywów.

Zakres:
1. Wydajność zamykania okna dialogowego (SettingsDialog.reject()):
   - Natychmiastowe zamykanie bez ponownego parsowania QSS, jeśli motyw nie był podglądany (< 50ms)
   - Brak podwójnego aplikowania motywu w _save_and_accept(), jeśli wybór się nie zmienił
2. Wydajność i izolacja pamięci podręcznej QSS:
   - Czas O(1) przy trafieniu w cache generate_theme_qss
   - Prawidłowe unieważnianie cache przez clear_theme_qss_cache()
   - Unikanie zbędnych wywołań setStyle("Fusion"), gdy styl jest już aktywny
3. Układ i responsywność kart motywów (ThemeCard):
   - Prawidłowe skalowanie kart przy skrajnych rozmiarach okna (400px–1440px) i czcionek (11px–24px)
   - Brak nakładania się kontrolek i kompaktowy rozmiar
"""

import os
import sys
import time
import json
import statistics
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from PySide6.QtWidgets import (
        QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
        QLabel, QStyle
    )
    from PySide6.QtCore import Qt, QRect, QSize
    from PySide6.QtGui import QFont, QFontMetrics
except ImportError:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
        QLabel, QStyle
    )
    from PyQt6.QtCore import Qt, QRect, QSize
    from PyQt6.QtGui import QFont, QFontMetrics

from recorder.ui import theme
from recorder.ui.appearance_tab import ThemeCard, AppearanceTab
from recorder.ui.settings_dialog import SettingsDialog
from recorder import config


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    tmp_file = os.path.join(str(tmp_path), "user_settings.json")
    initial = {
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
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(initial, f, indent=2)

    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_file)
    monkeypatch.setattr(config, "_CACHED_USER_SETTINGS", None)
    monkeypatch.setattr(config, "_CACHED_SETTINGS_TIME", 0.0)
    return tmp_file


# ==============================================================================
# 1. BENCHMARK DIALOG CLOSING PERFORMANCE
# ==============================================================================

class TestDialogClosingPerformance:

    def test_benchmark_reject_unpreviewed_vs_previewed(self, qapp, isolated_settings):
        """
        Benchmark SettingsDialog.reject() execution time across multiple iterations:
        - Unpreviewed: skips apply_theme, terminates instantaneously (< 50ms, typically < 5ms).
        - Previewed: invokes apply_theme to revert, takes significantly longer.
        Collects empirical timing statistics (min, mean, median, p95, max).
        """
        num_unpreviewed_iterations = 5
        unpreviewed_times_ms = []
        previewed_times_ms = []

        # 1. Unpreviewed reject() benchmark: measure repeated instant reject() on a single dialog.
        # Reusing the dialog instance avoids SettingsDialog creation overhead and prevents
        # Qt widget accumulation / O(N^2) stylesheet restyling cascades.
        dlg = SettingsDialog()
        assert getattr(dlg, "_theme_previewed", False) is False

        for _ in range(num_unpreviewed_iterations):
            t0 = time.perf_counter()
            dlg.reject()
            t_elapsed = (time.perf_counter() - t0) * 1000.0
            unpreviewed_times_ms.append(t_elapsed)

        # 2. Previewed reject() benchmark: preview an alternative theme, then measure revert on reject().
        dlg._on_theme_preview("emanager_light")
        assert getattr(dlg, "_theme_previewed", False) is True

        t0 = time.perf_counter()
        dlg.reject()
        t_elapsed = (time.perf_counter() - t0) * 1000.0
        previewed_times_ms.append(t_elapsed)
        dlg.deleteLater()

        # Clean up theme back to classic_dark
        theme.apply_theme(qapp, "classic_dark")

        # Empirical statistics
        unpreviewed_min = min(unpreviewed_times_ms)
        unpreviewed_mean = statistics.mean(unpreviewed_times_ms)
        unpreviewed_median = statistics.median(unpreviewed_times_ms)
        unpreviewed_p95 = sorted(unpreviewed_times_ms)[int(len(unpreviewed_times_ms) * 0.95)]
        unpreviewed_max = max(unpreviewed_times_ms)

        previewed_min = min(previewed_times_ms)
        previewed_mean = statistics.mean(previewed_times_ms)
        previewed_median = statistics.median(previewed_times_ms)

        print(f"\n--- DIALOG REJECT TIMING BENCHMARK (Unpreviewed N={num_unpreviewed_iterations}, Previewed N={len(previewed_times_ms)}) ---")
        print(f"Unpreviewed reject() ms: min={unpreviewed_min:.3f}, mean={unpreviewed_mean:.3f}, "
              f"median={unpreviewed_median:.3f}, p95={unpreviewed_p95:.3f}, max={unpreviewed_max:.3f}")
        print(f"Previewed reject() ms: min={previewed_min:.3f}, mean={previewed_mean:.3f}, "
              f"median={previewed_median:.3f}")

        # Strict empirical assertions
        assert unpreviewed_max < 50.0, f"Unpreviewed reject max was {unpreviewed_max:.2f}ms, expected < 50ms"
        assert unpreviewed_mean < 5.0, f"Unpreviewed reject mean was {unpreviewed_mean:.2f}ms, expected < 5ms"
        # Previewed reject must perform actual revert work
        assert previewed_mean > unpreviewed_mean, "Previewed reject should include theme restoration work"

    def test_save_and_accept_no_double_apply_when_matching(self, qapp, isolated_settings, monkeypatch):
        """
        Verify SettingsDialog._save_and_accept() avoids double-applying theme:
        - Scenario 1: Without preview, saved theme matches initial -> apply_theme NOT called.
        - Scenario 2: With preview to 'emanager_dark', saving 'emanager_dark' -> apply_theme NOT called.
        - Scenario 3: Target theme differs from current preview -> apply_theme IS called once.
        """
        from recorder.ui import theme as theme_module

        apply_mock = MagicMock()
        monkeypatch.setattr(theme_module, "apply_theme", apply_mock)

        # Scenario 1: Unpreviewed, theme remains classic_dark
        dlg1 = SettingsDialog()
        assert dlg1._theme_previewed is False

        apply_mock.reset_mock()
        dlg1._save_and_accept()
        assert apply_mock.call_count == 0, "apply_theme must not be called when saved theme matches initial"
        dlg1.deleteLater()

        # Scenario 2: Previewed emanager_dark via UI card click, and saved target is emanager_dark
        dlg2 = SettingsDialog()
        dlg2.appearance_tab._on_card_clicked("emanager_dark")
        dlg2.appearance_tab.slider_font_size.setValue(14)
        assert dlg2._theme_previewed is True
        assert dlg2.appearance_tab._active_theme_id == "emanager_dark"
        assert dlg2.appearance_tab.get_settings()["font_size"] == 14

        apply_mock.reset_mock()
        dlg2._save_and_accept()
        assert apply_mock.call_count == 0, (
            "apply_theme must not be called when previewed theme and font already match saved target"
        )
        dlg2.deleteLater()

        # Scenario 3: Target differs from preview (e.g. previewed emanager_dark, but saved target is classic_light)
        dlg3 = SettingsDialog()
        dlg3.appearance_tab._on_card_clicked("emanager_dark")
        assert dlg3._theme_previewed is True

        orig_get_settings = dlg3.appearance_tab.get_settings
        monkeypatch.setattr(dlg3.appearance_tab, "get_settings", lambda: {
            **orig_get_settings(),
            "theme": "classic_light",
            "font_size": 14
        })

        apply_mock.reset_mock()
        dlg3._save_and_accept()
        assert apply_mock.call_count == 1, "apply_theme must be called exactly once when target differs from preview"
        args, kwargs = apply_mock.call_args
        assert args[1] == "classic_light"
        assert kwargs.get("font_size") == 14
        dlg3.deleteLater()


# ==============================================================================
# 2. BENCHMARK QSS CACHING
# ==============================================================================

class TestQssCachingPerformance:

    def test_generate_theme_qss_caching_o1_benchmark(self):
        """
        Verify generate_theme_qss() caching performance:
        - Cold run: uncached template formatting.
        - Warm run: 10,000 cached lookups executing in O(1) time (< 10µs per lookup).
        - Identity: cached object identity preserved.
        """
        theme.clear_theme_qss_cache()

        # Cold call
        t0 = time.perf_counter()
        qss_cold = theme.generate_theme_qss("emanager_dark", font_size=15)
        cold_time_us = (time.perf_counter() - t0) * 1_000_000.0

        # Warm iterations
        n_iters = 10_000
        t0 = time.perf_counter()
        for _ in range(n_iters):
            qss_warm = theme.generate_theme_qss("emanager_dark", font_size=15)
        warm_total_us = (time.perf_counter() - t0) * 1_000_000.0
        warm_avg_us = warm_total_us / n_iters

        print(f"\n--- QSS CACHING BENCHMARK (N={n_iters}) ---")
        print(f"Cold QSS generation time: {cold_time_us:.2f} µs")
        print(f"Warm cached QSS lookup: avg={warm_avg_us:.3f} µs (total={warm_total_us/1000.0:.2f} ms)")

        assert qss_warm is qss_cold, "Cached QSS must return exact same string object"
        assert warm_avg_us < 10.0, f"Warm cache lookup took {warm_avg_us:.2f} µs, expected < 10 µs (O(1))"
        assert cold_time_us > warm_avg_us * 5, "Cold generation should take significantly longer than cache hit"

    def test_cache_invalidation_and_multi_key_isolation(self):
        """
        Verify cache isolation across different combinations and clear_theme_qss_cache() isolation:
        - Different themes produce distinct cache entries.
        - Different font sizes produce distinct cache entries.
        - clear_theme_qss_cache() clears all entries.
        """
        theme.clear_theme_qss_cache()
        assert len(theme._THEME_QSS_CACHE) == 0

        # Populate cache with several combinations
        qss_cd_13 = theme.generate_theme_qss("classic_dark", font_size=13)
        qss_cd_15 = theme.generate_theme_qss("classic_dark", font_size=15)
        qss_cl_13 = theme.generate_theme_qss("classic_light", font_size=13)
        qss_ed_13 = theme.generate_theme_qss("emanager_dark", font_size=13)
        qss_el_13 = theme.generate_theme_qss("emanager_light", font_size=13)

        assert len(theme._THEME_QSS_CACHE) == 5
        assert qss_cd_13 != qss_cd_15
        assert qss_cd_13 != qss_cl_13
        assert qss_ed_13 != qss_el_13

        # Test cache invalidation
        theme.clear_theme_qss_cache()
        assert len(theme._THEME_QSS_CACHE) == 0

        # Regeneration after clear
        qss_cd_13_new = theme.generate_theme_qss("classic_dark", font_size=13)
        assert len(theme._THEME_QSS_CACHE) == 1
        assert qss_cd_13_new == qss_cd_13

    def test_setup_theme_palette_avoids_repeated_set_style_when_fusion(self, qapp, monkeypatch):
        """
        Verify setup_theme_palette() guard:
        When current style objectName is 'fusion', setStyle('Fusion') must NOT be re-called.
        """
        # Clear stylesheet so app.style() returns the naked QFusionStyle (objectName == 'fusion')
        qapp.setStyleSheet("")
        qapp.setStyle("Fusion")
        assert qapp.style().objectName().lower() == "fusion"

        set_style_spy = MagicMock()
        monkeypatch.setattr(qapp, "setStyle", set_style_spy)

        for theme_id in ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]:
            theme.setup_theme_palette(qapp, theme_id)

        assert set_style_spy.call_count == 0, (
            f"app.setStyle('Fusion') should NOT be called when style is already Fusion! "
            f"Called {set_style_spy.call_count} times."
        )


# ==============================================================================
# 3. STRESS-TEST THEME CARD LAYOUT RESILIENCE
# ==============================================================================

class TestThemeCardLayoutResilience:

    def test_theme_card_has_no_lbl_desc(self):
        """
        Verify ThemeCard has completely removed lbl_desc, and retains:
        - lbl_name
        - lbl_badge
        - swatch_bg
        - swatch_accent
        - swatch_text
        - setToolTip(theme_def.description)
        """
        for th_id, th_def in theme.THEMES.items():
            card = ThemeCard(th_def)
            assert not hasattr(card, "lbl_desc"), f"ThemeCard for {th_id} must not have lbl_desc"
            assert hasattr(card, "lbl_name")
            assert hasattr(card, "lbl_badge")
            assert hasattr(card, "swatch_bg")
            assert hasattr(card, "swatch_accent")
            assert hasattr(card, "swatch_text")
            assert card.toolTip() == th_def.description
            card.deleteLater()

    def test_theme_card_size_hint_compact(self):
        """
        Verify ThemeCard sizeHint height remains compact (60px-70px) across all themes,
        preventing dialog layout explosion.
        """
        for th_id, th_def in theme.THEMES.items():
            card = ThemeCard(th_def)
            sh = card.sizeHint()
            assert 50 <= sh.height() <= 75, f"ThemeCard {th_id} sizeHint height was {sh.height()}px, expected 50-75px"
            card.deleteLater()

    def test_theme_card_layout_in_appearance_tab_across_extreme_sizes(self, qapp):
        """
        Stress test AppearanceTab across extreme dialog dimensions:
        Heights: 400px up to 1440px
        Widths: 400px up to 1920px
        Verifies:
        - ThemeCard heights stay compact (60px-75px) even at 1440px container height.
        - No widget overlaps between card title and active badge.
        - Swatches are positioned correctly below title and do not overlap.
        - No negative dimensions.
        """
        test_dimensions = [
            (400, 400),
            (500, 500),
            (600, 600),
            (800, 768),
            (1024, 900),
            (1280, 1080),
            (1920, 1440),
        ]

        for width, height in test_dimensions:
            tab = AppearanceTab()
            tab.resize(width, height)
            tab.show()
            qapp.processEvents()

            for th_id, card in tab.theme_cards.items():
                card_geom = card.geometry()
                assert card_geom.width() > 0, f"Card width was {card_geom.width()} at ({width}x{height})"
                assert 55 <= card_geom.height() <= 80, (
                    f"Card height was {card_geom.height()} at ({width}x{height}), expected compact 55-80px"
                )

                # Geometry checks on internal widgets
                name_rect = card.lbl_name.geometry()
                badge_rect = card.lbl_badge.geometry()
                assert name_rect.width() > 0 and name_rect.height() > 0
                assert badge_rect.width() >= 0 and badge_rect.height() > 0

                # Check swatches
                s_bg = card.swatch_bg.geometry()
                s_acc = card.swatch_accent.geometry()
                s_txt = card.swatch_text.geometry()

                assert s_bg.width() == 22 and s_bg.height() == 22
                assert s_acc.width() == 22 and s_acc.height() == 22
                assert s_txt.width() == 22 and s_txt.height() == 22

                # Swatches horizontal ordering
                assert s_bg.x() < s_acc.x() < s_txt.x(), "Swatches must be horizontally ordered"
                assert not s_bg.intersects(s_acc), "swatch_bg must not overlap swatch_accent"
                assert not s_acc.intersects(s_txt), "swatch_accent must not overlap swatch_text"

                # Swatches must be below the top title row
                assert s_bg.y() >= name_rect.bottom(), (
                    f"Swatches (y={s_bg.y()}) overlap vertically with title (bottom={name_rect.bottom()})"
                )

                # Active state check
                card.set_active(True)
                qapp.processEvents()
                assert "Aktywny" in card.lbl_badge.text()
                if card_geom.width() >= 180:
                    assert not card.lbl_name.geometry().intersects(card.lbl_badge.geometry()), (
                        f"lbl_name overlaps lbl_badge in card {th_id} at ({width}x{height})"
                    )

                card.set_active(False)
                qapp.processEvents()
                assert card.lbl_badge.text() == ""

            tab.close()
            tab.deleteLater()
