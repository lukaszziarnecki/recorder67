"""
Modułowa zakładka 'Wygląd & Personalizacja' dla okna SettingsDialog.

Zawiera:
1. Wizualny selektor motywów (4 interaktywne karty ThemeCard z próbkami barw i podświetleniem).
2. Sekcję 'Podgląd i Czytelność' (suwak rozmiaru czcionki 11-17px, format timestampów, kolejność i autoscroll).
3. Sekcję 'Zachowanie okna' (Always on Top, minimalizacja do zasobnika systemowego).
"""

from typing import Callable, Dict, Optional

try:
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtGui import QColor, QFont, QCursor
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
        QSlider, QComboBox, QCheckBox, QGroupBox, QFrame, QSizePolicy
    )
except ImportError:
    from PyQt6.QtCore import Qt, pyqtSignal as Signal
    from PyQt6.QtGui import QColor, QFont, QCursor
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
        QSlider, QComboBox, QCheckBox, QGroupBox, QFrame, QSizePolicy
    )

from recorder.ui.theme import THEMES, ThemeDefinition, get_available_themes


class ThemeCard(QFrame):
    """
    Wizualna, klikalna karta wyboru motywu z próbkami kolorów i wskaźnikiem aktywacji.
    """
    clicked = Signal(str)

    def __init__(self, theme_def: ThemeDefinition, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.theme_def = theme_def
        self.theme_id = theme_def.id
        self._is_active = False

        self.setObjectName(f"ThemeCard_{self.theme_id}")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setToolTip(self.theme_def.description)
        self._setup_ui()
        self._update_card_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Górny wiersz: Tytuł motywu + wskaźnik aktywacji
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.lbl_name = QLabel(self.theme_def.name)
        font = QFont("Segoe UI", 11, QFont.Weight.Bold)
        self.lbl_name.setFont(font)
        top_row.addWidget(self.lbl_name)

        top_row.addStretch()

        self.lbl_badge = QLabel("Aktywny" if self._is_active else "")
        self.lbl_badge.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        top_row.addWidget(self.lbl_badge)
        layout.addLayout(top_row)

        # Pasek próbek barw (Swatch bar)
        swatch_layout = QHBoxLayout()
        swatch_layout.setSpacing(6)

        self.swatch_bg = self._create_swatch(self.theme_def.swatch_bg, "Tło")
        self.swatch_accent = self._create_swatch(self.theme_def.swatch_accent, "Akcent")
        self.swatch_text = self._create_swatch(self.theme_def.swatch_text, "Tekst")

        swatch_layout.addWidget(self.swatch_bg)
        swatch_layout.addWidget(self.swatch_accent)
        swatch_layout.addWidget(self.swatch_text)
        swatch_layout.addStretch()
        layout.addLayout(swatch_layout)

    def _create_swatch(self, color_hex: str, tooltip: str) -> QLabel:
        sw = QLabel()
        sw.setFixedSize(22, 22)
        sw.setToolTip(tooltip)
        sw.setStyleSheet(
            f"background-color: {color_hex}; "
            f"border: 1px solid rgba(128, 128, 128, 0.4); "
            f"border-radius: 11px;"
        )
        return sw

    def set_active(self, active: bool):
        self._is_active = active
        self.lbl_badge.setText("● Aktywny" if active else "")
        self._update_card_style()

    def _update_card_style(self):
        bg = self.theme_def.bg_surface
        text = self.theme_def.text_primary
        border_color = self.theme_def.accent if self._is_active else self.theme_def.border
        border_width = "2px" if self._is_active else "1px"
        badge_color = self.theme_def.accent

        self.setStyleSheet(f"""
            QFrame#ThemeCard_{self.theme_id} {{
                background-color: {bg};
                border: {border_width} solid {border_color};
                border-radius: 8px;
            }}
            QFrame#ThemeCard_{self.theme_id}:hover {{
                border-color: {self.theme_def.accent};
            }}
        """)
        self.lbl_name.setStyleSheet(f"color: {text}; border: none; background: transparent;")
        self.lbl_badge.setStyleSheet(f"color: {badge_color}; border: none; background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.theme_id)
        super().mousePressEvent(event)


class AppearanceTab(QWidget):
    """
    Zakładka konfiguracyjna 'Wygląd & Personalizacja'.
    """
    theme_preview_requested = Signal(str)
    font_size_preview_requested = Signal(int)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        settings: Optional[dict] = None,
        on_theme_preview: Optional[Callable[[str], None]] = None,
        on_font_size_preview: Optional[Callable[[int], None]] = None,
    ):
        super().__init__(parent)
        self._settings = dict(settings or {})
        self._on_theme_preview_cb = on_theme_preview
        self._on_font_size_preview_cb = on_font_size_preview

        self._active_theme_id = self._settings.get("theme", "classic_dark")
        self._font_size = int(self._settings.get("font_size", self._settings.get("transcript_font_size", 13)))

        self.theme_cards: Dict[str, ThemeCard] = {}

        self._setup_ui()
        self.load_settings(self._settings)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(14)

        # ---------------------------------------------------------------------
        # 1. Sekcja: Wybór Motywu Aplikacji
        # ---------------------------------------------------------------------
        grp_theme = QGroupBox("🎨 Wybór Motywu Aplikacji")
        grp_theme.setObjectName("GrpThemeSelection")
        theme_grid = QGridLayout(grp_theme)
        theme_grid.setSpacing(10)
        theme_grid.setContentsMargins(10, 14, 10, 10)

        theme_ids = ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]
        for idx, th_id in enumerate(theme_ids):
            th_def = THEMES.get(th_id)
            if not th_def:
                continue
            card = ThemeCard(th_def, parent=self)
            card.clicked.connect(self._on_card_clicked)
            row, col = divmod(idx, 2)
            theme_grid.addWidget(card, row, col)
            self.theme_cards[th_id] = card

        main_layout.addWidget(grp_theme)

        # ---------------------------------------------------------------------
        # 2. Sekcja: Podgląd i Czytelność Transkrypcji
        # ---------------------------------------------------------------------
        grp_view = QGroupBox("🔤 Podgląd i Czytelność")
        grp_view.setObjectName("GrpPreviewOptions")
        view_layout = QVBoxLayout(grp_view)
        view_layout.setSpacing(10)
        view_layout.setContentsMargins(10, 14, 10, 10)

        # Suwak rozmiaru czcionki
        font_row = QHBoxLayout()
        lbl_font_title = QLabel("Rozmiar tekstu podglądu:")
        lbl_font_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        font_row.addWidget(lbl_font_title)

        self.slider_font_size = QSlider(Qt.Orientation.Horizontal)
        self.slider_font_size.setObjectName("slider_font_size")
        self.slider_font_size.setRange(11, 17)
        self.slider_font_size.setValue(self._font_size)
        self.slider_font_size.setTickInterval(1)
        self.slider_font_size.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_font_size.valueChanged.connect(self._on_font_slider_changed)
        font_row.addWidget(self.slider_font_size, stretch=1)

        self.lbl_font_size_val = QLabel(f"{self._font_size} px")
        self.lbl_font_size_val.setFixedWidth(45)
        self.lbl_font_size_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        font_row.addWidget(self.lbl_font_size_val)
        view_layout.addLayout(font_row)

        # Format timestampów
        ts_row = QHBoxLayout()
        lbl_ts = QLabel("Format timestampów:")
        lbl_ts.setFont(QFont("Segoe UI", 9))
        ts_row.addWidget(lbl_ts)

        self.combo_timestamp_format = QComboBox()
        self.combo_timestamp_format.setObjectName("combo_timestamp_format")
        self.combo_timestamp_format.addItem("Tylko offset [00:12 - 00:18] (Domyślne)", "offset_only")
        self.combo_timestamp_format.addItem("Offset + Godzina realna [00:12 | 13:47:12]", "offset+clock")
        self.combo_timestamp_format.addItem("Tylko godzina realna [13:47:12 - 13:47:18]", "clock_only")
        ts_row.addWidget(self.combo_timestamp_format, stretch=1)
        view_layout.addLayout(ts_row)

        # Kolejność podglądu
        order_row = QHBoxLayout()
        lbl_order = QLabel("Kolejność w podglądzie:")
        lbl_order.setFont(QFont("Segoe UI", 9))
        order_row.addWidget(lbl_order)

        self.combo_preview_order = QComboBox()
        self.combo_preview_order.setObjectName("combo_preview_order")
        self.combo_preview_order.addItem("Od najnowszych", "newest_first")
        self.combo_preview_order.addItem("Od najstarszych", "chronological")
        order_row.addWidget(self.combo_preview_order, stretch=1)
        view_layout.addLayout(order_row)

        # Auto-scroll
        self.chk_auto_scroll = QCheckBox("Automatycznie przewijaj widok do najnowszych wypowiedzi")
        self.chk_auto_scroll.setObjectName("chk_auto_scroll")
        self.combo_preview_order.currentIndexChanged.connect(self._on_preview_order_changed)
        view_layout.addWidget(self.chk_auto_scroll)

        main_layout.addWidget(grp_view)

        # ---------------------------------------------------------------------
        # 3. Sekcja: Zachowanie Okna
        # ---------------------------------------------------------------------
        grp_window = QGroupBox("Zachowanie okna")
        grp_window.setObjectName("GrpWindowBehavior")
        window_layout = QVBoxLayout(grp_window)
        window_layout.setSpacing(8)
        window_layout.setContentsMargins(10, 14, 10, 10)

        self.chk_always_on_top = QCheckBox("Okno zawsze na wierzchu")
        self.chk_always_on_top.setObjectName("chk_always_on_top")
        self.chk_always_on_top.setToolTip("Dyktafon pozostaje widoczny nad innymi oknami programu")
        window_layout.addWidget(self.chk_always_on_top)

        self.chk_minimize_to_tray = QCheckBox("Minimalizuj do zasobnika systemowego przy zamykaniu")
        self.chk_minimize_to_tray.setObjectName("chk_minimize_to_tray")
        self.chk_minimize_to_tray.setToolTip("Chroni wielogodzinne nagranie przed przypadkowym przerwaniem")
        window_layout.addWidget(self.chk_minimize_to_tray)

        main_layout.addWidget(grp_window)
        main_layout.addStretch()

    # -------------------------------------------------------------------------
    # Obsługa zdarzeń i interfejsu
    # -------------------------------------------------------------------------

    def _on_card_clicked(self, theme_id: str):
        self._active_theme_id = theme_id
        for tid, card in self.theme_cards.items():
            card.set_active(tid == theme_id)

        # Emisja sygnału podglądu na żywo
        self.theme_preview_requested.emit(theme_id)
        if self._on_theme_preview_cb:
            self._on_theme_preview_cb(theme_id)

    def _on_font_slider_changed(self, value: int):
        self._font_size = value
        self.lbl_font_size_val.setText(f"{value} px")
        self.font_size_preview_requested.emit(value)
        if self._on_font_size_preview_cb:
            self._on_font_size_preview_cb(value)

    def _on_preview_order_changed(self):
        """Włącza autoscroll tylko dla widoku chronologicznego (od najstarszych)."""
        is_chrono = (self.combo_preview_order.currentData() == "chronological")
        self.chk_auto_scroll.setEnabled(is_chrono)

    # -------------------------------------------------------------------------
    # Kontrakt danych
    # -------------------------------------------------------------------------

    def load_settings(self, settings: dict):
        """Ładuje stan kontrolek z przekazanego słownika ustawień."""
        self._settings = dict(settings or {})

        self._active_theme_id = self._settings.get("theme", "classic_dark")
        for tid, card in self.theme_cards.items():
            card.set_active(tid == self._active_theme_id)

        self._font_size = int(self._settings.get("font_size", self._settings.get("transcript_font_size", 13)))
        self.slider_font_size.setValue(max(11, min(17, self._font_size)))
        self.lbl_font_size_val.setText(f"{self.slider_font_size.value()} px")

        # Timestamp format
        ts_fmt = self._settings.get("timestamp_format", "offset_only")
        idx_ts = self.combo_timestamp_format.findData(ts_fmt)
        if idx_ts >= 0:
            self.combo_timestamp_format.setCurrentIndex(idx_ts)

        # Preview order
        order = self._settings.get("preview_order", "newest_first")
        idx_order = self.combo_preview_order.findData(order)
        if idx_order >= 0:
            self.combo_preview_order.setCurrentIndex(idx_order)

        # Checkboxes
        self.chk_auto_scroll.setChecked(bool(self._settings.get("auto_scroll_chronological", True)))
        self.chk_always_on_top.setChecked(bool(self._settings.get("always_on_top", False)))
        self.chk_minimize_to_tray.setChecked(bool(self._settings.get("minimize_to_tray_on_close", False)))
        self._on_preview_order_changed()

    def get_settings(self) -> dict:
        """Zwraca bieżące ustawienia z zakładki."""
        return {
            "theme": self._active_theme_id,
            "font_size": self.slider_font_size.value(),
            "transcript_font_size": self.slider_font_size.value(),
            "timestamp_format": self.combo_timestamp_format.currentData() or "offset_only",
            "preview_order": self.combo_preview_order.currentData() or "newest_first",
            "auto_scroll_chronological": self.chk_auto_scroll.isChecked(),
            "always_on_top": self.chk_always_on_top.isChecked(),
            "minimize_to_tray_on_close": self.chk_minimize_to_tray.isChecked(),
        }

    def reset_to_defaults(self):
        """Przywraca wartości domyślne zakładki."""
        defaults = {
            "theme": "classic_dark",
            "font_size": 13,
            "transcript_font_size": 13,
            "timestamp_format": "offset_only",
            "preview_order": "newest_first",
            "auto_scroll_chronological": True,
            "always_on_top": False,
            "minimize_to_tray_on_close": False,
        }
        self.load_settings(defaults)
        self._on_card_clicked("classic_dark")
        self._on_font_slider_changed(13)
