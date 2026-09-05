import os
import sys
import tempfile
from typing import Optional
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTabWidget, QWidget, QTextEdit, QTextBrowser, QComboBox,
    QSlider, QSpinBox, QCheckBox, QGroupBox, QFormLayout,
    QMessageBox, QFrame, QSizePolicy, QProgressBar, QScrollArea
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QUrl
from PySide6.QtGui import QFont, QIcon, QDesktopServices

from recorder.config import (
    load_user_settings,
    save_user_settings,
    WHISPER_MODELS,
    DEFAULT_WHISPER_MODEL,
    RecordSourceMode,
    APP_VERSION,
    GITHUB_REPO
)
from recorder.core.updater import (
    CheckUpdateWorker,
    DownloadUpdateWorker,
    apply_in_place_update
)
from recorder.core.logger import open_logs_folder


class MarkdownChangelogBrowser(QTextBrowser):
    """
    Rozszerzona kontrolka QTextBrowser dla opisów zmian (Changelog):
    1. Automatycznie normalizuje i czyści Markdown (m.in. naprawa urwanych linków compare GitHub).
    2. Zapobiega wyciekowi wewnętrznego bloku styli CSS Qt ('p, li { white-space: pre-wrap; } ...')
       do schowka systemowego podczas kopiowania tekstu (zarówno przez skrót Ctrl+C, menu kontekstowe, jak i metodę copy()).
    """
    def createMimeDataFromSelection(self):
        mime = super().createMimeDataFromSelection()
        if mime is not None and mime.hasHtml():
            import re
            from PySide6.QtCore import QMimeData
            raw_html = mime.html()
            clean_html = re.sub(r'<style[^>]*>.*?</style>', '', raw_html, flags=re.DOTALL)
            new_mime = QMimeData()
            if mime.hasText():
                new_mime.setText(mime.text())
            new_mime.setHtml(clean_html)
            return new_mime
        return mime

    def copy(self):
        mime = self.createMimeDataFromSelection()
        if mime is not None:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setMimeData(mime)
        else:
            super().copy()

    def setMarkdown(self, markdown: str):
        from recorder.core.updater import sanitize_changelog_markdown
        super().setMarkdown(sanitize_changelog_markdown(markdown))


class SettingsDialog(QDialog):
    """
    Nowoczesne okno ustawień aplikacji:
    - Karta 1: Słownik branżowy (słowa kluczowe), wybór Beam Size Whispera, Token HuggingFace
    - Karta 2: Źródła audio, czułość Silero VAD (mikrofon + system/Discord), auto-pauza i dzielenie sesji
    - Karta 3: Integracja chmurowa (Supabase / EMANAGER.PRO / Webhook), nazwa stanowiska
    """
    settings_saved_signal = pyqtSignal(dict)

    PRESET_KEYWORDS_IT = (
        "emanager.pro, EMANAGER.PRO, CRM, AI, Supabase, n8n, Make, webhook, API, LLM, GPT-4, Claude, Gemini, "
        "Gemini Vision, Lovable, React, Helpdesk, Subiekt GT, Subiekt, faktura proforma, zamówienia, zgłoszenia, "
        "harmonogram, kategorie, dyplomy, matryca uprawnień, recepcja, check-in, QR code, CSV, oświetleniowiec, "
        "synchronizacja, rejestr zmian, diaryzacja, transkrypcja, procesy biznesowe, architektura wzrostu"
    )

    PRESET_KEYWORDS_SALES = (
        "oferta handlowa, kosztorys, negocjacje, umowa, aneks, marża, rabat, "
        "klient, spotkanie zarządu, termin płatności, faktura VAT, zamówienie"
    )

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Ustawienia Dyktafonu AI")
        from recorder.ui.windows_integration import get_app_icon_path
        from recorder.config import get_theme, get_font_size
        self._initial_theme = get_theme()
        self._initial_font_size = get_font_size()
        self._theme_previewed = False

        ico = get_app_icon_path("ico")
        if ico and os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))
        self.setMinimumSize(640, 560)
        self.resize(680, 600)
        self._setup_ui()
        self._load_values()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # Nagłówek okna
        header_layout = QHBoxLayout()
        lbl_title = QLabel("⚙️ Konfiguracja & Preferencje AI")
        lbl_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        lbl_title.setObjectName("LblSettingsHeaderTitle")
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Zakładki
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SettingsTabWidget")

        self._create_tab_dictionary()
        self._create_tab_vad()
        self._create_tab_appearance()
        self._create_tab_cloud()
        self._create_tab_updates()

        main_layout.addWidget(self.tabs, stretch=1)

        # Dolny pasek przycisków
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(10)

        self.btn_restore_defaults = QPushButton("🔄 Przywróć Domyślne")
        self.btn_restore_defaults.clicked.connect(self._restore_defaults)
        btn_bar.addWidget(self.btn_restore_defaults)

        btn_bar.addStretch()

        self.btn_cancel = QPushButton("Anuluj")
        self.btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("💾 Zapisz Ustawienia")
        self.btn_save.setObjectName("BtnSave")
        self.btn_save.clicked.connect(self._save_and_accept)
        btn_bar.addWidget(self.btn_save)

        main_layout.addLayout(btn_bar)

    def _create_tab_dictionary(self):
        """Karta 1: Słownik branżowy, Beam Size Whispera, Token HuggingFace."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Sekcja: Słownik Branżowy
        box_dict = QGroupBox("📚 Słownik Słów Branżowych & Nazw Własnych (Initial Prompt)")
        dict_layout = QVBoxLayout(box_dict)

        lbl_dict_info = QLabel(
            "Wpisz specyficzne nazwy firm, programów, procesorów lub pojęcia branżowe "
            "(oddzielone przecinkami). Model Whisper traktuje je jako priorytetowy kontekst fonetyczny:"
        )
        lbl_dict_info.setWordWrap(True)
        lbl_dict_info.setObjectName("LblSettingDesc")
        dict_layout.addWidget(lbl_dict_info)

        self.txt_keywords = QTextEdit()
        self.txt_keywords.setPlaceholderText("np. Aldent, Subiekt GT, faktura proforma, i5-11400, CRM, Helpdesk...")
        self.txt_keywords.setMaximumHeight(90)
        dict_layout.addWidget(self.txt_keywords)

        # Przyciski szablonów
        preset_layout = QHBoxLayout()
        lbl_presets = QLabel("Szybkie szablony:")
        lbl_presets.setObjectName("LblSettingDesc")
        preset_layout.addWidget(lbl_presets)

        btn_preset_it = QPushButton("+ Szablon IT & Biuro")
        btn_preset_it.setObjectName("BtnPreset")
        btn_preset_it.clicked.connect(lambda: self._append_preset(self.PRESET_KEYWORDS_IT))
        preset_layout.addWidget(btn_preset_it)

        btn_preset_sales = QPushButton("+ Szablon Sprzedaż")
        btn_preset_sales.setObjectName("BtnPreset")
        btn_preset_sales.clicked.connect(lambda: self._append_preset(self.PRESET_KEYWORDS_SALES))
        preset_layout.addWidget(btn_preset_sales)

        btn_clear_dict = QPushButton("Wyczyść")
        btn_clear_dict.setObjectName("BtnDanger")
        btn_clear_dict.clicked.connect(self.txt_keywords.clear)
        preset_layout.addWidget(btn_clear_dict)

        preset_layout.addStretch()
        dict_layout.addLayout(preset_layout)
        layout.addWidget(box_dict)

        # Sekcja: Dokładność Whispera (Beam Size)
        box_whisper = QGroupBox("🎯 Precyzja Transkrypcji Whispera (Beam Search)")
        whisper_layout = QVBoxLayout(box_whisper)

        beam_row = QHBoxLayout()
        lbl_beam = QLabel("Tryb przeszukiwania hipotez (Beam Size):")
        beam_row.addWidget(lbl_beam)

        self.combo_beam = QComboBox()
        self.combo_beam.addItem("⚡ Szybki (Beam Size = 1) - minimalne użycie CPU", 1)
        self.combo_beam.addItem("⚖️ Zrównoważony (Beam Size = 3) - dobry balans", 3)
        self.combo_beam.addItem("🚀 Maksymalna Dokładność (Beam Size = 5) [Zalecany]", 5)
        beam_row.addWidget(self.combo_beam)
        whisper_layout.addLayout(beam_row)

        lbl_beam_desc = QLabel(
            "Większy Beam Size analizuje kilka ścieżek zdań jednocześnie, całkowicie eliminując "
            "błędy fonetyczne i przekręcanie związków frazeologicznych bez widocznego narzutu na czas."
        )
        lbl_beam_desc.setWordWrap(True)
        lbl_beam_desc.setObjectName("LblSettingDesc")
        whisper_layout.addWidget(lbl_beam_desc)

        self.chk_adaptive_beam = QCheckBox("🚀 Automatyczny bieg turbo (Adaptacyjny Beam Size przy zatorach w kolejce)")
        self.chk_adaptive_beam.setToolTip(
            "Opcja zalecana podczas wielogodzinnych maratonów (4h–8h) na słabszych procesorach.\n"
            "Gdy w kolejce transkrypcji powstanie opóźnienie (więcej niż 1 blok), tymczasowo redukuje parametr beam_size=1,\n"
            "aby błyskawicznie nadgonić nagranie i odciążyć CPU kosztem nieco niższej precyzji w trudnych warunkach (cichy głos/szum).\n"
            "Domyślnie wyłączona w celu zapewnienia stałej, maksymalnej dokładności modelu Whisper."
        )
        whisper_layout.addWidget(self.chk_adaptive_beam)
        layout.addWidget(box_whisper)

        # Sekcja: Token HuggingFace
        box_hf = QGroupBox("🔑 Dostęp do Rozpoznawania Osób (PyAnnote HuggingFace)")
        hf_layout = QVBoxLayout(box_hf)

        hf_input_row = QHBoxLayout()
        self.txt_hf_token = QLineEdit()
        self.txt_hf_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_hf_token.setPlaceholderText("hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        hf_input_row.addWidget(self.txt_hf_token, stretch=1)

        self.btn_toggle_hf = QPushButton("👁️ Pokaż")
        self.btn_toggle_hf.clicked.connect(self._toggle_hf_visibility)
        hf_input_row.addWidget(self.btn_toggle_hf)
        hf_layout.addLayout(hf_input_row)

        lbl_hf_info = QLabel("Token wymagany do pobrania modeli diaryzacji mowy (pyannote/speaker-diarization-3.1).")
        lbl_hf_info.setObjectName("LblSettingDesc")
        hf_layout.addWidget(lbl_hf_info)
        layout.addWidget(box_hf)

        layout.addStretch()
        self.tabs.addTab(tab, "📚 Słownik i AI")

    def _create_tab_vad(self):
        """Karta 2: Źródła audio, czułość VAD dla mikrofonu i systemu oraz czasy sesji."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        # Sekcja: Domyślne Źródło Audio
        box_source = QGroupBox("Domyślne Źródło Dźwięku")
        source_layout = QFormLayout(box_source)
        source_layout.setSpacing(10)

        self.combo_default_source_mode = QComboBox()
        self.combo_default_source_mode.addItem("🎙️+🎧 Mikrofon + Dźwięk Systemu", RecordSourceMode.HYBRID_DUAL)
        self.combo_default_source_mode.addItem("🎙️ Tylko Mikrofon", RecordSourceMode.MIC_ONLY)
        self.combo_default_source_mode.addItem("🎧 Tylko Dźwięk Systemu", RecordSourceMode.SYSTEM_ONLY)
        source_layout.addRow(QLabel("Tryb nagrywania:"), self.combo_default_source_mode)
        layout.addWidget(box_source)

        # Sekcja: Czułość VAD Mikrofonu
        box_vad = QGroupBox("🎙️ Czułość Detekcji Mowy Mikrofonu")
        vad_layout = QVBoxLayout(box_vad)

        slider_row = QHBoxLayout()
        self.slider_vad = QSlider(Qt.Orientation.Horizontal)
        self.slider_vad.setRange(20, 60)
        self.slider_vad.setValue(42)
        self.slider_vad.setTickInterval(5)
        self.slider_vad.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_vad.valueChanged.connect(self._on_vad_slider_changed)
        slider_row.addWidget(self.slider_vad, stretch=1)

        self.lbl_vad_val = QLabel("0.42 (Zalecany)")
        self.lbl_vad_val.setObjectName("LblVadVal")
        slider_row.addWidget(self.lbl_vad_val)
        vad_layout.addLayout(slider_row)
        layout.addWidget(box_vad)

        # Sekcja: Czułość VAD Dźwięku Systemu
        box_vad_sys = QGroupBox("🎧 Czułość Detekcji Dźwięku Systemu")
        vad_sys_layout = QVBoxLayout(box_vad_sys)

        sys_slider_row = QHBoxLayout()
        self.slider_vad_sys = QSlider(Qt.Orientation.Horizontal)
        self.slider_vad_sys.setRange(20, 60)
        self.slider_vad_sys.setValue(42)
        self.slider_vad_sys.setTickInterval(5)
        self.slider_vad_sys.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_vad_sys.valueChanged.connect(self._on_vad_sys_slider_changed)
        sys_slider_row.addWidget(self.slider_vad_sys, stretch=1)

        self.lbl_vad_sys_val = QLabel("0.42 (Zalecany)")
        self.lbl_vad_sys_val.setObjectName("LblVadSysVal")
        sys_slider_row.addWidget(self.lbl_vad_sys_val)
        vad_sys_layout.addLayout(sys_slider_row)
        layout.addWidget(box_vad_sys)

        # Sekcja: Czasy i sesje
        box_time = QGroupBox("⏱️ Zarządzanie Ciszą i Sesjami Nagrywania")
        time_layout = QFormLayout(box_time)
        time_layout.setSpacing(12)

        self.spin_auto_pause = QSpinBox()
        self.spin_auto_pause.setRange(1, 15)
        self.spin_auto_pause.setValue(5)
        self.spin_auto_pause.setSuffix(" sek.")
        time_layout.addRow(QLabel("Czas ciszy do automatycznej pauzy:"), self.spin_auto_pause)

        self.combo_session_split = QComboBox()
        self.combo_session_split.addItem("10 minut ciągłej ciszy", 600.0)
        self.combo_session_split.addItem("15 minut ciągłej ciszy (Zalecane)", 900.0)
        self.combo_session_split.addItem("30 minut ciągłej ciszy", 1800.0)
        self.combo_session_split.addItem("1 godzina ciągłej ciszy", 3600.0)
        self.combo_session_split.addItem("Wyłączone (Zawsze jedna długa sesja)", 0.0)
        time_layout.addRow(QLabel("Automatyczny podział sesji:"), self.combo_session_split)

        self.combo_silence_alert = QComboBox()
        self.combo_silence_alert.addItem("1 minuta braku głosu (Szybki alert)", 1.0)
        self.combo_silence_alert.addItem("2 minuty braku głosu", 2.0)
        self.combo_silence_alert.addItem("3 minuty braku głosu", 3.0)
        self.combo_silence_alert.addItem("5 minut braku głosu (Zalecane / Domyślne)", 5.0)
        self.combo_silence_alert.addItem("10 minut braku głosu", 10.0)
        self.combo_silence_alert.addItem("15 minut braku głosu", 15.0)
        self.combo_silence_alert.addItem("20 minut braku głosu", 20.0)
        self.combo_silence_alert.addItem("Wyłączone (Brak ostrzeżeń)", 0.0)
        time_layout.addRow(QLabel("Ostrzeżenie o braku dźwięku:"), self.combo_silence_alert)

        self.btn_test_alert = QPushButton("🔔 Przetestuj powiadomienie")
        self.btn_test_alert.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test_alert.setObjectName("BtnPreset")
        self.btn_test_alert.clicked.connect(self._on_test_alert_clicked)
        time_layout.addRow("", self.btn_test_alert)

        layout.addWidget(box_time)
        layout.addStretch()
        self.tabs.addTab(tab, "🎙️ Audio i VAD")

    def _create_tab_appearance(self):
        """Karta 3: Wygląd i Personalizacja (Motywy, Czytelność, Okno)."""
        from recorder.ui.appearance_tab import AppearanceTab
        from recorder.config import load_user_settings
        st = load_user_settings()
        self.appearance_tab = AppearanceTab(
            parent=self,
            settings=st,
            on_theme_preview=self._on_theme_preview,
            on_font_size_preview=self._on_font_size_preview,
        )
        self.tab_appearance = self.appearance_tab
        # Aliasy dla kompatybilności wstecznej
        self.combo_timestamp_format = self.appearance_tab.combo_timestamp_format
        self.combo_preview_order = self.appearance_tab.combo_preview_order
        self.chk_auto_scroll = self.appearance_tab.chk_auto_scroll
        self.tabs.addTab(self.appearance_tab, "🎨 Wygląd i Personalizacja")

    def _create_tab_cloud(self):
        """Karta 3: Chmura, Supabase, Stanowisko i Webhook."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        box_ident = QGroupBox("💻 Identyfikacja Stanowiska Komputerowego")
        ident_layout = QFormLayout(box_ident)

        self.txt_device_name = QLineEdit()
        self.txt_device_name.setPlaceholderText("np. Biuro-Adrian / Sala-Konferencyjna-1")
        ident_layout.addRow(QLabel("Nazwa Stanowiska:"), self.txt_device_name)

        self.txt_org_id = QLineEdit()
        self.txt_org_id.setPlaceholderText("default_org / emanager_main")
        ident_layout.addRow(QLabel("ID Organizacji:"), self.txt_org_id)
        layout.addWidget(box_ident)

        box_sync = QGroupBox("☁️ Cel Synchronizacji Chmurowej (CRM / n8n / Supabase)")
        sync_layout = QFormLayout(box_sync)

        self.combo_sync_target = QComboBox()
        self.combo_sync_target.addItem("EMANAGER.PRO (Bezpośrednio do bazy Supabase)", "emanager")
        self.combo_sync_target.addItem("Własny Webhook (n8n / Make / Zapier)", "generic_webhook")
        self.combo_sync_target.addItem("Wyłączona (Tylko pliki lokalne)", "none")
        sync_layout.addRow(QLabel("Cel wysyłki danych:"), self.combo_sync_target)

        self.txt_supabase_url = QLineEdit()
        self.txt_supabase_url.setPlaceholderText("https://xyz.supabase.co")
        sync_layout.addRow(QLabel("Adres Supabase URL:"), self.txt_supabase_url)

        self.txt_supabase_key = QLineEdit()
        self.txt_supabase_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_supabase_key.setPlaceholderText("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...")
        sync_layout.addRow(QLabel("Klucz Supabase Key:"), self.txt_supabase_key)

        self.txt_webhook_url = QLineEdit()
        self.txt_webhook_url.setPlaceholderText("https://twoj-serwer-n8n.pl/webhook/meeting-ingest")
        sync_layout.addRow(QLabel("Adres Webhook URL:"), self.txt_webhook_url)

        self.chk_auto_sync = QCheckBox("Automatycznie wysyłaj transkrypcję po zakończeniu nagrania")
        sync_layout.addRow("", self.chk_auto_sync)

        self.chk_upload_audio = QCheckBox("Dołączaj plik dźwiękowy WAV do chmury (umożliwia odsłuch)")
        sync_layout.addRow("", self.chk_upload_audio)

        layout.addWidget(box_sync)
        layout.addStretch()
        self.tabs.addTab(tab, "☁️ Chmura i Stanowisko")

    def _append_preset(self, preset_text: str):
        cur = self.txt_keywords.toPlainText().strip()
        if cur:
            if not cur.endswith(","):
                cur += ","
            self.txt_keywords.setPlainText(f"{cur} {preset_text}")
        else:
            self.txt_keywords.setPlainText(preset_text)

    def _toggle_hf_visibility(self):
        if self.txt_hf_token.echoMode() == QLineEdit.EchoMode.Password:
            self.txt_hf_token.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_hf.setText("🙈 Ukryj")
        else:
            self.txt_hf_token.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_hf.setText("👁️ Pokaż")

    def _on_vad_slider_changed(self, val: int):
        f_val = val / 100.0
        if f_val < 0.32:
            desc = f"{f_val:.2f} (Wysoka czułość / Szept)"
        elif f_val <= 0.45:
            desc = f"{f_val:.2f} (Zalecany / Biuro)"
        else:
            desc = f"{f_val:.2f} (Tłumienie hałasu)"
        self.lbl_vad_val.setText(desc)

    def _on_vad_sys_slider_changed(self, val: int):
        f_val = val / 100.0
        if f_val < 0.32:
            desc = f"{f_val:.2f} (Cichy głos online)"
        elif f_val <= 0.45:
            desc = f"{f_val:.2f} (Zalecany)"
        else:
            desc = f"{f_val:.2f} (Silne tłumienie zakłóceń)"
        self.lbl_vad_sys_val.setText(desc)

    def _on_preview_order_changed(self):
        is_chrono = (self.combo_preview_order.currentData() == "chronological")
        self.chk_auto_scroll.setEnabled(is_chrono)

    def _on_test_alert_clicked(self):
        mins = float(self.combo_silence_alert.currentData() if self.combo_silence_alert.currentData() is not None else 5.0)
        silence_sec = 300.0 if mins <= 0.0 else mins * 60.0
        if self.parent() and hasattr(self.parent(), "show_silence_alert_preview"):
            self.parent().show_silence_alert_preview(silence_sec)
        else:
            QMessageBox.information(self, "Test powiadomienia", "Wysłano testowe powiadomienie o braku dźwięku.")

    def _create_tab_updates(self):
        """Karta 4: Aktualizacje programu z GitHub Releases."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Informacja o bieżącej wersji
        grp_cur = QGroupBox("📌 Informacje o Aplikacji")
        cur_layout = QFormLayout(grp_cur)
        cur_layout.setContentsMargins(12, 12, 12, 12)
        cur_layout.setSpacing(10)

        lbl_v = QLabel(f"<b>v{APP_VERSION}</b>")
        lbl_v.setStyleSheet("font-size: 13px; color: #10b981;")
        cur_layout.addRow("Zainstalowana wersja:", lbl_v)

        lbl_repo = QLabel(f"<code>{GITHUB_REPO}</code>")
        lbl_repo.setObjectName("LblSettingDesc")
        cur_layout.addRow("Repozytorium wydań:", lbl_repo)

        self.chk_auto_check_startup = QCheckBox("Sprawdzaj dostępność aktualizacji automatycznie przy starcie aplikacji")
        self.chk_auto_check_startup.setChecked(True)
        cur_layout.addRow("", self.chk_auto_check_startup)

        self.chk_check_prereleases = QCheckBox("Uwzględniaj wersje testowe (Pre-release / Alpha / Beta)")
        self.chk_check_prereleases.setChecked(True)
        cur_layout.addRow("", self.chk_check_prereleases)

        layout.addWidget(grp_cur)

        # Pasek sprawdzania aktualizacji
        check_box = QHBoxLayout()
        self.btn_check_updates = QPushButton("🔍 Sprawdź dostępność aktualizacji")
        self.btn_check_updates.setObjectName("BtnCheckUpdates")
        self.btn_check_updates.clicked.connect(self._on_check_updates_clicked)
        check_box.addWidget(self.btn_check_updates)

        self.lbl_update_status = QLabel("Kliknij przycisk, aby sprawdzić najnowsze wydanie na GitHubie.")
        self.lbl_update_status.setWordWrap(True)
        self.lbl_update_status.setObjectName("LblSettingDesc")
        check_box.addWidget(self.lbl_update_status, stretch=1)
        layout.addLayout(check_box)

        # Pasek postępu pobierania
        self.progress_download = QProgressBar()
        self.progress_download.setRange(0, 100)
        self.progress_download.setTextVisible(True)
        self.progress_download.setVisible(False)
        layout.addWidget(self.progress_download)

        # Ramka z informacjami o nowej wersji (domyślnie ukryta)
        self.grp_new_version = QGroupBox("🎉 Dostępna nowa wersja!")
        new_v_layout = QVBoxLayout(self.grp_new_version)
        new_v_layout.setContentsMargins(12, 12, 12, 12)
        new_v_layout.setSpacing(8)

        self.lbl_new_version_title = QLabel("")
        self.lbl_new_version_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.lbl_new_version_title.setWordWrap(True)
        self.lbl_new_version_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        new_v_layout.addWidget(self.lbl_new_version_title)

        # Wybór wersji changelogu (szczególnie przydatne, gdy użytkownik jest o kilka wersji do tyłu)
        self.version_select_row = QHBoxLayout()
        self.lbl_select_version = QLabel("Wyświetlane zmiany:")
        self.lbl_select_version.setObjectName("LblSettingDesc")
        self.combo_changelog_version = QComboBox()
        self.combo_changelog_version.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_changelog_version.setMinimumContentsLength(10)
        self.combo_changelog_version.currentIndexChanged.connect(self._on_changelog_version_changed)
        self.version_select_row.addWidget(self.lbl_select_version)
        self.version_select_row.addWidget(self.combo_changelog_version, stretch=1)
        new_v_layout.addLayout(self.version_select_row)

        self.txt_changelog = MarkdownChangelogBrowser()
        self.txt_changelog.setReadOnly(True)
        self.txt_changelog.setOpenExternalLinks(True)
        self.txt_changelog.setMinimumHeight(240)
        new_v_layout.addWidget(self.txt_changelog)

        btn_row = QHBoxLayout()
        self.btn_download_update = QPushButton("🚀 Pobierz i zainstaluj aktualizację")
        self.btn_download_update.setObjectName("BtnSave")
        self.btn_download_update.clicked.connect(self._on_download_update_clicked)
        btn_row.addWidget(self.btn_download_update)

        self.btn_open_release_url = QPushButton("🌐 Strona wydania na GitHubie")
        self.btn_open_release_url.clicked.connect(self._on_open_release_url_clicked)
        btn_row.addWidget(self.btn_open_release_url)
        btn_row.addStretch()

        new_v_layout.addLayout(btn_row)
        layout.addWidget(self.grp_new_version)
        self.grp_new_version.setVisible(False)

        # Przycisk opcjonalnego rozwinięcia pełnej historii zmian
        self.btn_toggle_history = QPushButton("📜 Pokaż także historię starszych wydań...")
        self.btn_toggle_history.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_history.setObjectName("BtnPreset")
        self.btn_toggle_history.clicked.connect(self._on_toggle_history_clicked)
        self.btn_toggle_history.setVisible(False)
        layout.addWidget(self.btn_toggle_history)

        # Sekcja historii wydań (dostępna także, gdy użytkownik jest na najnowszej wersji)
        self.grp_history = QGroupBox("📜 Historia Wydań i Zmian (Changelog)")
        history_layout = QVBoxLayout(self.grp_history)
        history_layout.setContentsMargins(12, 12, 12, 12)
        history_layout.setSpacing(8)

        hist_select_row = QHBoxLayout()
        lbl_hist_version = QLabel("Wybierz wersję:")
        lbl_hist_version.setObjectName("LblSettingDesc")
        self.combo_history_version = QComboBox()
        self.combo_history_version.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_history_version.setMinimumContentsLength(10)
        self.combo_history_version.currentIndexChanged.connect(self._on_history_version_changed)
        hist_select_row.addWidget(lbl_hist_version)
        hist_select_row.addWidget(self.combo_history_version, stretch=1)

        self.btn_open_history_url = QPushButton("🌐 Strona tego wydania")
        self.btn_open_history_url.clicked.connect(self._on_open_history_url_clicked)
        hist_select_row.addWidget(self.btn_open_history_url)
        history_layout.addLayout(hist_select_row)

        self.txt_history_changelog = MarkdownChangelogBrowser()
        self.txt_history_changelog.setReadOnly(True)
        self.txt_history_changelog.setOpenExternalLinks(True)
        self.txt_history_changelog.setMinimumHeight(200)
        history_layout.addWidget(self.txt_history_changelog)
        layout.addWidget(self.grp_history)
        self.grp_history.setVisible(False)

        # Sekcja diagnostyki i logów
        self.grp_diagnostics = QGroupBox("🛠️ Diagnostyka i Dzienniki Zdarzeń (Logi)")
        diag_layout = QVBoxLayout(self.grp_diagnostics)
        diag_layout.setContentsMargins(12, 12, 12, 12)
        diag_layout.setSpacing(10)

        lbl_diag_desc = QLabel("Zdarzenia i ewentualne błędy są automatycznie zapisywane do pliku logs/app.log (z bezpiecznym maskowaniem kluczy API).")
        lbl_diag_desc.setWordWrap(True)
        lbl_diag_desc.setObjectName("LblSettingDesc")
        diag_layout.addWidget(lbl_diag_desc)

        btn_diag_row = QHBoxLayout()
        self.btn_open_logs = QPushButton("📁 Otwórz folder z logami")
        self.btn_open_logs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_logs.clicked.connect(self._on_open_logs_clicked)
        btn_diag_row.addWidget(self.btn_open_logs)
        btn_diag_row.addStretch()
        diag_layout.addLayout(btn_diag_row)
        layout.addWidget(self.grp_diagnostics)

        layout.addStretch()
        scroll.setWidget(tab)
        self.tabs.addTab(scroll, "🚀 Aktualizacje")

    def _on_check_updates_clicked(self):
        self.btn_check_updates.setEnabled(False)
        self.lbl_update_status.setText("⏳ Sprawdzanie wydań na GitHubie...")
        self.lbl_update_status.setStyleSheet("color: #4cc9f0; font-size: 11px;")
        
        self.check_worker = CheckUpdateWorker(include_prereleases=self.chk_check_prereleases.isChecked())
        self.check_worker.update_checked_signal.connect(self._on_update_check_result)
        self.check_worker.error_signal.connect(self._on_update_check_error)
        self.check_worker.start()

    def _on_toggle_history_clicked(self):
        """Przełącza widoczność sekcji historii wydań, gdy dostępna jest nowa wersja."""
        now_visible = not self.grp_history.isVisible()
        self.grp_history.setVisible(now_visible)
        if now_visible:
            self.btn_toggle_history.setText("🙈 Ukryj historię starszych wydań")
        else:
            self.btn_toggle_history.setText("📜 Pokaż także historię starszych wydań...")

    def _populate_history_combo(self, all_rels: list):
        """Wypełnia listę rozwijaną historii wydań i ustawia treść Markdown."""
        self.combo_history_version.blockSignals(True)
        self.combo_history_version.clear()
        for rel in all_rels:
            r_tag = rel.get("tag_name", "")
            r_date = rel.get("published_at", "")[:10]
            d_str = f" ({r_date})" if r_date else ""
            cur_mark = " (Zainstalowana wersja)" if r_tag.lstrip("vV") == APP_VERSION.lstrip("vV") else ""
            self.combo_history_version.addItem(
                f"{r_tag}{cur_mark}{d_str}",
                {"notes": rel.get("release_notes", ""), "url": rel.get("html_url", "")}
            )
        self.combo_history_version.blockSignals(False)

        if self.combo_history_version.count() > 0:
            self.combo_history_version.setCurrentIndex(0)
            init_h = self.combo_history_version.itemData(0)
            if isinstance(init_h, dict):
                self.txt_history_changelog.setMarkdown(init_h.get("notes", ""))
                self._selected_history_url = init_h.get("url", "")

    def _on_changelog_version_changed(self, idx: int):
        """Przełącza treść Markdown w oknie nowej wersji w zależności od wyboru w comboboxie."""
        if idx < 0:
            return
        data = self.combo_changelog_version.itemData(idx)
        if isinstance(data, dict):
            notes = data.get("notes", "")
            self._selected_changelog_url = data.get("url", "")
            self.txt_changelog.setMarkdown(notes)

    def _on_history_version_changed(self, idx: int):
        """Przełącza treść Markdown w oknie historii wydań."""
        if idx < 0:
            return
        data = self.combo_history_version.itemData(idx)
        if isinstance(data, dict):
            notes = data.get("notes", "")
            self._selected_history_url = data.get("url", "")
            self.txt_history_changelog.setMarkdown(notes)

    def _on_open_history_url_clicked(self):
        """Otwiera w przeglądarce stronę wybranego wydania z historii."""
        url = getattr(self, "_selected_history_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _on_open_logs_clicked(self):
        """Otwiera folder logs/ w Eksploratorze Windows."""
        open_logs_folder()

    def _on_update_check_result(self, result: Optional[dict]):
        self.btn_check_updates.setEnabled(True)
        if result and result.get("has_update"):
            self.latest_update_data = result
            tag = result.get("latest_version", "")
            title = result.get("release_title", tag)
            is_pre = result.get("is_prerelease", False)
            pre_badge = " (Pre-release)" if is_pre else ""
            
            newer_rels = result.get("newer_releases", [])
            count_newer = len(newer_rels)
            
            if count_newer > 1:
                self.lbl_update_status.setText(f"✅ Znaleziono nowe wydanie: {tag}{pre_badge} (jesteś o {count_newer} wydań do tyłu)")
            else:
                self.lbl_update_status.setText(f"✅ Znaleziono nowe wydanie: {tag}{pre_badge}")
            self.lbl_update_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")
            
            self.lbl_new_version_title.setText(f"Wydanie docelowe: {title}")

            # Wypełnienie wyboru wersji (zsumowane vs poszczególne)
            self.combo_changelog_version.blockSignals(True)
            self.combo_changelog_version.clear()

            if count_newer > 1:
                agg_notes = result.get("aggregated_notes", "")
                self.combo_changelog_version.addItem(
                    f"📋 Zsumowane zmiany ze wszystkich brakujących wydań ({result.get('current_version', APP_VERSION)} ➔ {tag})",
                    {"notes": agg_notes, "url": result.get("html_url", "")}
                )

            for rel in newer_rels:
                r_tag = rel.get("tag_name", "")
                r_date = rel.get("published_at", "")[:10]
                d_str = f" ({r_date})" if r_date else ""
                latest_mark = " (Najnowsza)" if r_tag == tag else ""
                self.combo_changelog_version.addItem(
                    f"Wydanie {r_tag}{latest_mark}{d_str}",
                    {"notes": rel.get("release_notes", ""), "url": rel.get("html_url", "")}
                )

            self.combo_changelog_version.blockSignals(False)

            # Ustawienie domyślnego widoku Markdown
            if self.combo_changelog_version.count() > 0:
                self.combo_changelog_version.setCurrentIndex(0)
                init_data = self.combo_changelog_version.itemData(0)
                if isinstance(init_data, dict):
                    self.txt_changelog.setMarkdown(init_data.get("notes", ""))
                    self._selected_changelog_url = init_data.get("url", result.get("html_url", ""))
            else:
                self.txt_changelog.setMarkdown(result.get("release_notes", ""))
                self._selected_changelog_url = result.get("html_url", "")

            self.grp_new_version.setVisible(True)
            self.grp_history.setVisible(False)

            # Przygotowanie pełnej historii w tle z przyciskiem opcjonalnego podglądu
            all_rels = result.get("all_releases", [])
            if all_rels:
                self._populate_history_combo(all_rels)
                self.btn_toggle_history.setVisible(True)
                self.btn_toggle_history.setText("📜 Pokaż także historię starszych wydań...")
            else:
                self.btn_toggle_history.setVisible(False)

            # Sprawdzenie czy paczka aktualizacji jest już pobrana w katalogu tymczasowym
            expected_zip = os.path.join(tempfile.gettempdir(), f"InteligentnyDyktafonAI_{tag}.zip")
            asset_size = result.get("asset_size", 0)
            if os.path.exists(expected_zip) and (asset_size == 0 or abs(os.path.getsize(expected_zip) - asset_size) < 4096):
                self._cached_zip_path = expected_zip
                self.btn_download_update.setText(f"⚡ Zainstaluj pobraną wersję {tag}")
                self.progress_download.setVisible(True)
                self.progress_download.setValue(100)
                self.lbl_update_status.setText(f"✅ Wersja {tag} jest już pobrana na dysku i gotowa do instalacji!")
                self.lbl_update_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")
            else:
                self._cached_zip_path = None
                self.btn_download_update.setText("🚀 Pobierz i zainstaluj aktualizację")
        else:
            self.grp_new_version.setVisible(False)
            self.btn_toggle_history.setVisible(False)
            self.lbl_update_status.setText(f"Posiadasz najnowszą wersję programu (v{APP_VERSION}).")
            self.lbl_update_status.setStyleSheet("color: #10b981; font-size: 11px;")

            # Wyświetlenie pełnej historii wydań, gdy brak nowszej wersji
            all_rels = (result.get("all_releases", []) if result else [])
            if all_rels:
                self._populate_history_combo(all_rels)
                self.grp_history.setVisible(True)
            else:
                self.grp_history.setVisible(False)

    def _on_update_check_error(self, err_msg: str):
        self.btn_check_updates.setEnabled(True)
        self.lbl_update_status.setText(f"⚠️ Nie udało się sprawdzić aktualizacji: {err_msg}")
        self.lbl_update_status.setStyleSheet("color: #ef4444; font-size: 11px;")

    def _on_open_release_url_clicked(self):
        url = getattr(self, "_selected_changelog_url", "") or getattr(self, "latest_update_data", {}).get("html_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _on_download_update_clicked(self):
        data = getattr(self, "latest_update_data", {})
        target_version = data.get("latest_version", "latest")
        
        # Jeśli plik aktualizacji został już wcześniej pobrany i leży w Temp
        if getattr(self, "_cached_zip_path", None) and os.path.exists(self._cached_zip_path):
            self._prompt_and_apply_update(self._cached_zip_path, target_version)
            return

        download_url = data.get("download_url")
        if not download_url:
            url = data.get("html_url", "")
            if url:
                QDesktopServices.openUrl(QUrl(url))
            return
            
        self.btn_download_update.setEnabled(False)
        self.progress_download.setVisible(True)
        self.progress_download.setValue(0)
        
        self.download_worker = DownloadUpdateWorker(download_url, target_version)
        self.download_worker.progress_signal.connect(self._on_download_progress)
        self.download_worker.download_finished_signal.connect(self._on_download_finished)
        self.download_worker.start()

    def _on_download_progress(self, pct: int, status_text: str):
        self.progress_download.setValue(pct)
        self.lbl_update_status.setText(status_text)

    def _on_download_finished(self, success: bool, path_or_err: str, version: str):
        self.btn_download_update.setEnabled(True)
        if success:
            self._cached_zip_path = path_or_err
            self.btn_download_update.setText(f"⚡ Zainstaluj pobraną wersję {version}")
            self.progress_download.setValue(100)
            self.lbl_update_status.setText("✅ Aktualizacja została pobrana.")
            self._prompt_and_apply_update(path_or_err, version)
        else:
            self.lbl_update_status.setText(f"❌ {path_or_err}")
            self.lbl_update_status.setStyleSheet("color: #ef4444; font-size: 11px;")

    def _prompt_and_apply_update(self, path_or_err: str, version: str):
        """Wyświetla nowoczesny polski dialog wyboru instalacji natychmiastowej lub przy wyjściu."""
        is_frozen = getattr(sys, "frozen", False)
        if is_frozen:
            dlg = UpdatePromptDialog(self, version)
            dlg.exec()
            
            if dlg.choice == "restart_now":
                applied = apply_in_place_update(path_or_err, restart_after=True)
                if applied:
                    from PySide6.QtCore import QCoreApplication
                    QCoreApplication.quit()
                    os._exit(0)
            elif dlg.choice == "on_exit":
                if self.parent() and hasattr(self.parent(), "set_pending_update"):
                    self.parent().set_pending_update(path_or_err, version)
                self.lbl_update_status.setText(f"💤 Wersja {version} zostanie zainstalowana po zamknięciu programu.")
                self.lbl_update_status.setStyleSheet("color: #4cc9f0; font-size: 11px;")
            else:
                self.lbl_update_status.setText("Paczka pozostaje pobrana. Możesz zainstalować ją w dowolnym momencie.")
                self.lbl_update_status.setStyleSheet("color: #8d99ae; font-size: 11px;")
        else:
            QMessageBox.information(
                self,
                "Pobrano Aktualizację",
                f"Pobrano paczkę aktualizacyjną do:\n{path_or_err}\n\n(Działasz w trybie deweloperskim ze skryptów Python)."
            )

    def _load_values(self):
        """Ładuje aktualne ustawienia do kontrolek UI."""
        st = load_user_settings()

        # Słownik i AI
        self.txt_keywords.setPlainText(st.get("custom_keywords", ""))
        beam_val = int(st.get("whisper_beam_size", 5))
        idx = self.combo_beam.findData(beam_val)
        if idx != -1:
            self.combo_beam.setCurrentIndex(idx)
        self.txt_hf_token.setText(st.get("hf_token", ""))
        self.chk_adaptive_beam.setChecked(bool(st.get("adaptive_beam_size", False)))

        # Źródło Audio & VAD
        src_mode = st.get("record_source_mode", RecordSourceMode.HYBRID_DUAL)
        sm_idx = self.combo_default_source_mode.findData(src_mode)
        if sm_idx != -1:
            self.combo_default_source_mode.setCurrentIndex(sm_idx)

        vad_val = int(float(st.get("vad_speech_threshold", 0.42)) * 100)
        self.slider_vad.setValue(vad_val)
        self._on_vad_slider_changed(vad_val)

        vad_sys_val = int(float(st.get("system_vad_speech_threshold", 0.42)) * 100)
        self.slider_vad_sys.setValue(vad_sys_val)
        self._on_vad_sys_slider_changed(vad_sys_val)

        self.spin_auto_pause.setValue(int(float(st.get("auto_pause_sec", 5.0))))

        split_sec = float(st.get("session_split_silence_sec", 900.0))
        s_idx = self.combo_session_split.findData(split_sec)
        if s_idx != -1:
            self.combo_session_split.setCurrentIndex(s_idx)

        alert_mins = float(st.get("silence_alert_minutes", 5.0))
        a_idx = self.combo_silence_alert.findData(alert_mins)
        if a_idx != -1:
            self.combo_silence_alert.setCurrentIndex(a_idx)
        else:
            self.combo_silence_alert.setCurrentIndex(self.combo_silence_alert.findData(5.0))

        ts_fmt = st.get("timestamp_format", "offset_only")
        ts_idx = self.combo_timestamp_format.findData(ts_fmt)
        if ts_idx != -1:
            self.combo_timestamp_format.setCurrentIndex(ts_idx)

        order = st.get("preview_order", "newest_first")
        o_idx = self.combo_preview_order.findData(order)
        if o_idx != -1:
            self.combo_preview_order.setCurrentIndex(o_idx)
        else:
            self.combo_preview_order.setCurrentIndex(0)

        self.chk_auto_scroll.setChecked(bool(st.get("auto_scroll_chronological", True)))
        self._on_preview_order_changed()

        # Chmura & Stanowisko
        self.txt_device_name.setText(st.get("device_name", "Biuro-Stanowisko-1"))
        self.txt_org_id.setText(st.get("organization_id", "default_org"))

        target = st.get("sync_target", "emanager")
        t_idx = self.combo_sync_target.findData(target)
        if t_idx != -1:
            self.combo_sync_target.setCurrentIndex(t_idx)

        self.txt_supabase_url.setText(st.get("supabase_url", ""))
        self.txt_supabase_key.setText(st.get("supabase_key", ""))
        self.txt_webhook_url.setText(st.get("generic_webhook_url", ""))
        self.chk_auto_sync.setChecked(bool(st.get("auto_cloud_sync", True)))
        self.chk_upload_audio.setChecked(bool(st.get("sync_upload_audio", False)))
        self.chk_check_prereleases.setChecked(bool(st.get("check_prereleases", True)))
        self.chk_auto_check_startup.setChecked(bool(st.get("auto_check_updates_startup", True)))

    def _on_theme_preview(self, theme_id: str):
        self._theme_previewed = True
        from recorder.ui.theme import apply_theme, set_window_titlebar_theme, THEMES
        font_size = self.appearance_tab.get_settings().get("font_size", 13) if hasattr(self, "appearance_tab") else 13
        apply_theme(QApplication.instance(), theme_id, font_size=font_size, hwnd=int(self.winId()))
        if self.parent() and hasattr(self.parent(), "winId"):
            th_def = THEMES.get(theme_id)
            if th_def:
                set_window_titlebar_theme(int(self.parent().winId()), th_def.is_dark)

    def _on_font_size_preview(self, font_size: int):
        self._theme_previewed = True
        from recorder.ui.theme import apply_theme
        theme_id = self.appearance_tab.get_settings().get("theme", "classic_dark") if hasattr(self, "appearance_tab") else "classic_dark"
        apply_theme(QApplication.instance(), theme_id, font_size=font_size, hwnd=int(self.winId()))

    def reject(self):
        if getattr(self, "_theme_previewed", False):
            from recorder.ui.theme import apply_theme, set_window_titlebar_theme, THEMES
            initial_theme = getattr(self, "_initial_theme", "classic_dark")
            initial_font_size = getattr(self, "_initial_font_size", 13)
            apply_theme(QApplication.instance(), initial_theme, font_size=initial_font_size, hwnd=int(self.winId()))
            if self.parent() and hasattr(self.parent(), "winId"):
                th_def = THEMES.get(initial_theme)
                if th_def:
                    set_window_titlebar_theme(int(self.parent().winId()), th_def.is_dark)
        super().reject()

    def select_tab(self, tab_id):
        """Przełącza aktywną zakładkę w oknie ustawień."""
        if isinstance(tab_id, int):
            self.tabs.setCurrentIndex(tab_id)
        elif str(tab_id).lower() in ("updates", "aktualizacje"):
            for i in range(self.tabs.count()):
                text = self.tabs.tabText(i).lower()
                if "aktualizacje" in text or "update" in text:
                    self.tabs.setCurrentIndex(i)
                    break
        elif str(tab_id).lower() in ("appearance", "wyglad", "wygląd", "personalizacja", "motyw", "theme"):
            for i in range(self.tabs.count()):
                text = self.tabs.tabText(i).lower()
                if "wygląd" in text or "personalizacja" in text or "appearance" in text:
                    self.tabs.setCurrentIndex(i)
                    break

    def _restore_defaults(self):
        """Przywraca zalecane wartości domyślne."""
        reply = QMessageBox.question(
            self,
            "Przywracanie Domyślnych",
            "Czy na pewno chcesz przywrócić domyślne ustawienia?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.txt_keywords.setPlainText(self.PRESET_KEYWORDS_IT)
            self.combo_beam.setCurrentIndex(self.combo_beam.findData(5))
            self.combo_default_source_mode.setCurrentIndex(self.combo_default_source_mode.findData(RecordSourceMode.HYBRID_DUAL))
            self.slider_vad.setValue(42)
            self.slider_vad_sys.setValue(42)
            self.spin_auto_pause.setValue(5)
            self.combo_session_split.setCurrentIndex(self.combo_session_split.findData(900.0))
            self.combo_silence_alert.setCurrentIndex(self.combo_silence_alert.findData(5.0))
            if hasattr(self, "appearance_tab"):
                self.appearance_tab.reset_to_defaults()
            self.chk_auto_sync.setChecked(True)
            self.chk_upload_audio.setChecked(False)
            self.chk_check_prereleases.setChecked(True)
            self.chk_auto_check_startup.setChecked(True)
            self.chk_adaptive_beam.setChecked(False)

    def _save_and_accept(self):
        """Zapisuje wartości do pliku user_settings.json i zamyka dialog."""
        new_settings = {
            "custom_keywords": self.txt_keywords.toPlainText().strip(),
            "whisper_beam_size": int(self.combo_beam.currentData() or 5),
            "adaptive_beam_size": self.chk_adaptive_beam.isChecked(),
            "hf_token": self.txt_hf_token.text().strip(),
            "record_source_mode": self.combo_default_source_mode.currentData() or RecordSourceMode.HYBRID_DUAL,
            "vad_speech_threshold": round(self.slider_vad.value() / 100.0, 2),
            "system_vad_speech_threshold": round(self.slider_vad_sys.value() / 100.0, 2),
            "auto_pause_sec": float(self.spin_auto_pause.value()),
            "session_split_silence_sec": float(self.combo_session_split.currentData() or 900.0),
            "silence_alert_minutes": float(self.combo_silence_alert.currentData() if self.combo_silence_alert.currentData() is not None else 5.0),
            "timestamp_format": self.combo_timestamp_format.currentData() or "offset_only",
            "preview_order": self.combo_preview_order.currentData() or "newest_first",
            "auto_scroll_chronological": self.chk_auto_scroll.isChecked(),
            "device_name": self.txt_device_name.text().strip() or "Biuro-Stanowisko-1",
            "organization_id": self.txt_org_id.text().strip() or "default_org",
            "sync_target": self.combo_sync_target.currentData() or "emanager",
            "supabase_url": self.txt_supabase_url.text().strip(),
            "supabase_key": self.txt_supabase_key.text().strip(),
            "generic_webhook_url": self.txt_webhook_url.text().strip(),
            "auto_cloud_sync": self.chk_auto_sync.isChecked(),
            "sync_upload_audio": self.chk_upload_audio.isChecked(),
            "check_prereleases": self.chk_check_prereleases.isChecked(),
            "auto_check_updates_startup": self.chk_auto_check_startup.isChecked(),
        }

        if hasattr(self, "appearance_tab"):
            new_settings.update(self.appearance_tab.get_settings())

        success = save_user_settings(new_settings)
        if success:
            target_theme = new_settings.get("theme", "classic_dark")
            target_font_size = new_settings.get("font_size", 13)

            current_preview_theme = getattr(self.appearance_tab, "_active_theme_id", None) if hasattr(self, "appearance_tab") else None
            current_preview_font_size = getattr(self.appearance_tab, "_font_size", None) if hasattr(self, "appearance_tab") else None

            if getattr(self, "_theme_previewed", False):
                needs_apply = (target_theme != current_preview_theme or target_font_size != current_preview_font_size)
            else:
                needs_apply = (target_theme != getattr(self, "_initial_theme", None) or
                               target_font_size != getattr(self, "_initial_font_size", None))

            if needs_apply:
                from recorder.ui.theme import apply_theme
                apply_theme(
                    QApplication.instance(),
                    target_theme,
                    font_size=target_font_size,
                    hwnd=int(self.winId())
                )
            self.settings_saved_signal.emit(new_settings)
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", "Nie udało się zapisać pliku ustawień user_settings.json.")


class UpdatePromptDialog(QDialog):
    """
    Nowoczesny, przestronny dialog wyboru sposobu instalacji aktualizacji.
    Eliminuje problem obcinania etykiet przycisków w QMessageBox.
    """
    def __init__(self, parent, version: str):
        super().__init__(parent)
        self.setWindowTitle("Aktualizacja Gotowa - Inteligentny Dyktafon AI")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        # Header z ikoną i opisem
        header = QHBoxLayout()
        header.setSpacing(16)
        lbl_icon = QLabel("🚀")
        lbl_icon.setStyleSheet("font-size: 32px;")
        header.addWidget(lbl_icon)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        lbl_title = QLabel(f"Pobrano aktualizację <b>{version}</b>")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        lbl_sub = QLabel("Wybierz, w jaki sposób chcesz zastosować nową wersję programu:")
        lbl_sub.setObjectName("LblSettingDesc")
        text_layout.addWidget(lbl_title)
        text_layout.addWidget(lbl_sub)
        header.addLayout(text_layout, stretch=1)
        layout.addLayout(header)

        # Przyciski ułożone pionowo – zero obcinania tekstu
        self.btn_restart_now = QPushButton("⚡  Zaktualizuj i zrestartuj teraz")
        self.btn_restart_now.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart_now.setObjectName("BtnSave")
        self.btn_restart_now.clicked.connect(self._choose_restart_now)
        layout.addWidget(self.btn_restart_now)

        self.btn_on_exit = QPushButton("💤  Zainstaluj przy zamknięciu programu")
        self.btn_on_exit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_on_exit.setObjectName("BtnPreset")
        self.btn_on_exit.clicked.connect(self._choose_on_exit)
        layout.addWidget(self.btn_on_exit)

        self.btn_later = QPushButton("Później (anuluj na razie)")
        self.btn_later.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_later.setObjectName("BtnCancel")
        self.btn_later.clicked.connect(self.reject)
        layout.addWidget(self.btn_later)

        self.choice = "later"

    def _choose_restart_now(self):
        self.choice = "restart_now"
        self.accept()

    def _choose_on_exit(self):
        self.choice = "on_exit"
        self.accept()

