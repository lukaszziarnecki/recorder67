import os
import sys
import time
import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger("recorder.ui.window")

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QDesktopServices, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QProgressBar, QListWidget,
    QListWidgetItem, QGroupBox, QMessageBox, QFrame,
    QSlider, QLineEdit, QTextEdit, QScrollArea, QFileDialog, QCheckBox,
    QSizePolicy, QDialog, QSystemTrayIcon, QMenu
)

from recorder.config import (
    SmartRecordState,
    RecordSourceMode,
    RECORDINGS_DIR,
    TRANSCRIPTIONS_DIR,
    get_hf_token,
    SAMPLE_RATE,
    DEFAULT_AUTO_PAUSE_SEC,
    WHISPER_MODELS,
    DEFAULT_WHISPER_MODEL,
    get_hardware_acceleration_info,
    SPEAKER_COUNT_OPTIONS,
    get_recommended_profile,
    load_user_settings,
    get_custom_keywords,
    get_vad_speech_threshold,
    get_system_vad_speech_threshold,
    get_record_source_mode,
    get_loopback_device_index,
    get_silence_alert_seconds,
    get_session_split_silence_sec,
    is_auto_check_updates_startup
)
from recorder.audio.devices import (
    get_working_input_devices,
    get_working_loopback_devices,
    get_active_audio_apps
)
from recorder.core.vad import is_silero_available
from recorder.ui.theme import DARK_THEME_QSS, setup_dark_palette
from recorder.ui.settings_dialog import SettingsDialog
from recorder.ui.workers import (
    SmartAudioWorker,
    TranscriptionWorker,
    FileProcessingWorker,
    DiarizationOnlyWorker
)
from recorder.core.rolling_transcriber import RollingTranscriptionWorker, RollingBlock
from recorder.core.speakers import (
    analyze_speakers,
    suggest_speaker_names,
    format_turns,
    parse_txt_to_turns,
    format_speaker_stats
)
from recorder.core.session import (
    TranscriptionSession,
    get_session_path_for_txt,
    get_session_path_for_audio,
    find_existing_session_for_audio,
    extract_datetime_from_filename,
    get_turn_sync_id
)
from recorder.core.cloud_sync import CloudSyncManager


class SilenceToastBanner(QWidget):
    """
    Dyskretny, kompaktowy baner powiadomienia (Toast) w prawym dolnym rogu ekranu.
    Zaprojektowany w stylu Windows 11 Fluent: odtwarza dźwięk systemowy, nie kradnie fokusu z aktywnego okna
    i w przypadku braku reakcji przekazuje powiadomienie do Centrum Akcji Windows.
    """
    confirmed = Signal()
    inspect_requested = Signal()
    dismissed = Signal()
    timed_out = Signal()

    def __init__(self, parent=None, silence_sec: float = 600.0, source_mode: str = RecordSourceMode.HYBRID_DUAL, timeout_sec: int = 45):
        super().__init__(None)
        self.silence_sec = silence_sec
        self.source_mode = source_mode
        self.remaining_sec = timeout_sec

        # Odtworzenie natywnego dźwięku systemowego Windows (chime powiadomienia)
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setFixedWidth(380)

        card = QFrame(self)
        card.setObjectName("ToastCard")
        card.setStyleSheet("""
            #ToastCard {
                background-color: #1e1e2f;
                border: 1px solid #3b82f6;
                border-radius: 10px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # 1. Nagłówek aplikacji (Ikona + Nazwa + Zamknij)
        app_header = QHBoxLayout()
        app_header.setSpacing(6)
        from recorder.ui.windows_integration import get_app_icon_path
        png_icon = get_app_icon_path("png")
        if png_icon and os.path.exists(png_icon):
            lbl_app_logo = QLabel()
            pix = QPixmap(png_icon).scaled(15, 15, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl_app_logo.setPixmap(pix)
            app_header.addWidget(lbl_app_logo)
        lbl_app_name = QLabel("Inteligentny Dyktafon AI")
        lbl_app_name.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        lbl_app_name.setStyleSheet("color: #8d99ae;")
        app_header.addWidget(lbl_app_name, stretch=1)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(22, 22)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setToolTip("Zamknij powiadomienie")
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94a3b8;
                border: none;
                padding: 0px;
                margin: 0px;
                font-size: 13px;
                font-weight: bold;
                border-radius: 11px;
            }
            QPushButton:hover {
                color: #ffffff;
                background-color: #ef4444;
            }
        """)
        btn_close.clicked.connect(self._on_close_clicked)
        app_header.addWidget(btn_close)
        layout.addLayout(app_header)

        # 2. Treść monitu (Ikona ostrzeżenia + Tytuł i krótki opis)
        content_row = QHBoxLayout()
        content_row.setSpacing(10)

        lbl_icon = QLabel("⚠️")
        lbl_icon.setFont(QFont("Segoe UI Emoji", 18))
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_row.addWidget(lbl_icon)

        mins = int(silence_sec // 60)
        mins_str = f"{mins} min" if mins > 0 else f"{int(silence_sec)} s"

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        lbl_title = QLabel(f"Brak dźwięku od {mins_str}")
        lbl_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        lbl_title.setStyleSheet("color: #f59e0b;")
        text_layout.addWidget(lbl_title)

        if source_mode == RecordSourceMode.SYSTEM_ONLY:
            desc_text = "Brak zarejestrowanego dźwięku z komputera."
        elif source_mode == RecordSourceMode.MIC_ONLY:
            desc_text = "Brak zarejestrowanej mowy z mikrofonu."
        else:
            desc_text = "Brak mowy w mikrofonie oraz dźwięku z systemu."

        lbl_desc = QLabel(desc_text)
        lbl_desc.setWordWrap(True)
        lbl_desc.setFont(QFont("Segoe UI", 8))
        lbl_desc.setStyleSheet("color: #cbd5e1;")
        text_layout.addWidget(lbl_desc)

        content_row.addLayout(text_layout, stretch=1)
        layout.addLayout(content_row)

        # 3. Pasek akcji (Licznik czasu + Dyskretne przyciski)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.lbl_timer = QLabel(f"Zniknie za {self.remaining_sec}s")
        self.lbl_timer.setFont(QFont("Segoe UI", 8))
        self.lbl_timer.setStyleSheet("color: #64748b;")
        action_row.addWidget(self.lbl_timer, stretch=1)

        self.btn_ok = QPushButton("Wszystko gra")
        self.btn_ok.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
        self.btn_ok.setFixedHeight(26)
        self.btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ok.setStyleSheet("""
            QPushButton {
                background-color: #252836;
                color: #e2e8f0;
                border: 1px solid #3a3f55;
                border-radius: 4px;
                padding: 3px 10px;
            }
            QPushButton:hover {
                background-color: #353a4e;
                color: #ffffff;
            }
        """)
        self.btn_ok.clicked.connect(self._on_ok_clicked)
        action_row.addWidget(self.btn_ok)

        self.btn_err = QPushButton("Sprawdź dźwięk")
        self.btn_err.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_err.setFixedHeight(26)
        self.btn_err.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_err.setStyleSheet("""
            QPushButton {
                background-color: #4361ee;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 3px 10px;
            }
            QPushButton:hover {
                background-color: #3a0ca3;
            }
        """)
        self.btn_err.clicked.connect(self._on_err_clicked)
        action_row.addWidget(self.btn_err)

        layout.addLayout(action_row)

        self._reposition()

        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(1000)
        self.auto_timer.timeout.connect(self._on_tick)
        self.auto_timer.start()

    def _reposition(self):
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.adjustSize()
            w = self.width()
            h = self.height()
            x = geom.right() - w - 24
            y = geom.bottom() - h - 24
            self.move(x, y)

    def _on_tick(self):
        self.remaining_sec -= 1
        if self.remaining_sec <= 0:
            self.auto_timer.stop()
            self.timed_out.emit()
            self.close()
        else:
            self.lbl_timer.setText(f"Zniknie za {self.remaining_sec}s")

    def _on_ok_clicked(self):
        self.auto_timer.stop()
        self.confirmed.emit()
        self.close()

    def _on_err_clicked(self):
        self.auto_timer.stop()
        self.inspect_requested.emit()
        self.close()

    def _on_close_clicked(self):
        self.auto_timer.stop()
        self.dismissed.emit()
        self.close()


class SmartDictaphoneWindow(QMainWindow):
    """
    Główne okno aplikacji Inteligentnego Dyktafonu AI (Ambient AI & Recorder).
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Inteligentny Dyktafon AI - Wykrywanie Mowy (VAD)")
        from recorder.ui.windows_integration import get_app_icon_path
        ico = get_app_icon_path("ico")
        if ico and os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))
        self.resize(780, 950)
        self.setMinimumSize(620, 720)

        self.recordings_dir = RECORDINGS_DIR
        self.transcriptions_dir = TRANSCRIPTIONS_DIR

        self.last_audio_save_path = None
        self.recorded_seconds = 0
        self._active_recorded_time = 0.0
        self._last_active_tick = None
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._on_timer_tick)

        self.live_transcription_worker = None
        self.live_plain_text_lines = []
        self.transcription_thread = None
        self.file_processing_worker = None

        # Stan mapowania mówców
        self.current_turns = []
        self.current_txt_path = None
        self.speaker_inputs = {}
        self.last_plain_text = ""
        self.current_meeting_id = None
        self.synced_segment_count = 0
        self._synced_turn_ids = set()
        self.last_processed_block_idx = 0
        self._active_threads = []
        self._finalize_pending = False       # Guard: blokuje Start gdy trwa finalizacja poprzedniej sesji
        self.session_start_time = None       # Realna godzina startu bieżącego nagrania (datetime)
        self._active_silence_dialog = None

        # Moduł Cloud Sync (Supabase / EMANAGER.PRO / Webhook)
        self.cloud_sync = CloudSyncManager()
        self.cloud_sync.signals.sync_started.connect(self._on_sync_started)
        self.cloud_sync.signals.sync_finished.connect(self._on_sync_finished)
        self.cloud_sync.signals.offline_queued.connect(self._on_offline_queued)
        self.cloud_sync.signals.live_session_started.connect(self._on_live_session_started)
        self.cloud_sync.signals.live_block_synced.connect(self._on_live_block_synced)
        self.cloud_sync.signals.live_session_finalized.connect(self._on_live_session_finalized)

        self.worker = SmartAudioWorker(samplerate=SAMPLE_RATE, auto_pause_sec=DEFAULT_AUTO_PAUSE_SEC)
        self.worker.audio_level_signal.connect(self._update_audio_level)
        self.worker.dual_audio_level_signal.connect(self._update_dual_audio_level)
        self.worker.vad_info_signal.connect(self._update_vad_info)
        self.worker.state_changed_signal.connect(self._on_worker_state_changed)
        self.worker.session_split_signal.connect(self._on_session_split_triggered)
        self.worker.silence_alert_signal.connect(self._on_silence_alert)
        self.worker.error_signal.connect(self._handle_audio_error)
        self.worker.set_session_split_silence_sec(get_session_split_silence_sec())

        self._init_ui()
        self._apply_theme()
        self._refresh_audio_devices()
        self._refresh_recordings_list()
        self._refresh_transcriptions_list()

        self._active_silence_toast = None
        self._active_silence_dialog = None
        self._setup_tray_icon()

        # Uruchomienie przetwarzania zaległej kolejki offline
        self.cloud_sync.process_offline_queue_async()

        # Oczekująca aktualizacja do zainstalowania przy wyjściu (Install on exit)
        self._pending_update_zip_path = None
        self._pending_update_version = None

        # Ciche sprawdzenie dostępności aktualizacji w tle przy starcie
        if is_auto_check_updates_startup():
            QTimer.singleShot(3500, self._start_silent_update_check)


    def _init_ui(self):
        scroll_area = QScrollArea()
        scroll_area.setObjectName("MainScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.setCentralWidget(scroll_area)

        main_widget = QWidget()
        main_widget.setObjectName("MainContainerWidget")
        scroll_area.setWidget(main_widget)

        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(22, 22, 22, 22)

        # NAGŁÓWEK Z PRZYCISKIEM USTAWIEŃ
        header_container = QHBoxLayout()
        
        header_text_layout = QVBoxLayout()
        title = QLabel("🎙️ Inteligentny Dyktafon AI")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        subtitle = QLabel("Detekcja Mowy (Silero VAD AI) & Faster-Whisper")
        subtitle.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        subtitle.setStyleSheet("color: #4cc9f0;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignLeft)

        header_text_layout.addWidget(title)
        header_text_layout.addWidget(subtitle)
        header_container.addLayout(header_text_layout, stretch=1)

        self.btn_settings = QPushButton("⚙️ Ustawienia")
        self.btn_settings.setToolTip("Otwórz słownik branżowy, parametry AI, VAD i chmury")
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background-color: #2b2d42;
                color: #4cc9f0;
                border: 1px solid #3d405b;
                border-radius: 8px;
                padding: 10px 18px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #3d405b;
                color: #edf2f4;
                border-color: #4cc9f0;
            }
        """)
        self.btn_settings.clicked.connect(self._open_settings_dialog)
        header_container.addWidget(self.btn_settings)

        main_layout.addLayout(header_container)

        # BANER AKTUALIZACJI (Domyślnie ukryty, pojawia się po cichym wykryciu aktualizacji w tle)
        self.banner_update = QFrame()
        self.banner_update.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a2238, stop:1 #283048);
                border: 1px solid #4361ee;
                border-radius: 8px;
            }
        """)
        banner_layout = QHBoxLayout(self.banner_update)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        banner_layout.setSpacing(10)

        self.lbl_update_banner_text = QLabel("🚀 Dostępna jest nowa wersja dyktafonu!")
        self.lbl_update_banner_text.setStyleSheet("color: #4cc9f0; font-size: 12px; font-weight: bold; border: none; background: transparent;")
        banner_layout.addWidget(self.lbl_update_banner_text, stretch=1)

        self.btn_update_banner_action = QPushButton("Pokaż aktualizację")
        self.btn_update_banner_action.setStyleSheet("""
            QPushButton {
                background-color: #4361ee;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3a0ca3;
            }
        """)
        self.btn_update_banner_action.clicked.connect(lambda: self._open_settings_dialog(initial_tab="updates"))
        banner_layout.addWidget(self.btn_update_banner_action)

        btn_close_banner = QPushButton("✕")
        btn_close_banner.setToolTip("Ukryj powiadomienie")
        btn_close_banner.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8d99ae;
                border: none;
                font-size: 13px;
                font-weight: bold;
                padding: 4px 8px;
            }
            QPushButton:hover {
                color: #edf2f4;
            }
        """)
        btn_close_banner.clicked.connect(self.banner_update.hide)
        banner_layout.addWidget(btn_close_banner)

        self.banner_update.hide()
        main_layout.addWidget(self.banner_update)

        # ŹRÓDŁA DŹWIĘKU
        sources_box = QGroupBox("Źródła Dźwięku")
        sources_layout = QVBoxLayout(sources_box)
        sources_layout.setSpacing(8)

        # 1. Wybór Trybu Źródła
        mode_row = QHBoxLayout()
        lbl_mode = QLabel("Tryb:")
        lbl_mode.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        lbl_mode.setStyleSheet("color: #4cc9f0; min-width: 120px;")
        
        self.combo_source_mode = QComboBox()
        self.combo_source_mode.addItem("🎙️+🎧 Mikrofon + Dźwięk Systemu", RecordSourceMode.HYBRID_DUAL)
        self.combo_source_mode.addItem("🎙️ Tylko Mikrofon", RecordSourceMode.MIC_ONLY)
        self.combo_source_mode.addItem("🎧 Tylko Dźwięk Systemu", RecordSourceMode.SYSTEM_ONLY)
        
        saved_mode = get_record_source_mode()
        sm_idx = self.combo_source_mode.findData(saved_mode)
        if sm_idx != -1:
            self.combo_source_mode.setCurrentIndex(sm_idx)
            
        self.combo_source_mode.currentIndexChanged.connect(self._on_source_mode_changed)
        mode_row.addWidget(lbl_mode)
        mode_row.addWidget(self.combo_source_mode, stretch=1)
        sources_layout.addLayout(mode_row)

        # 2. Wybór Mikrofonu
        mic_row = QHBoxLayout()
        self.lbl_mic_input = QLabel("🎙️ Mikrofon:")
        self.lbl_mic_input.setFont(QFont("Segoe UI", 9))
        self.lbl_mic_input.setStyleSheet("min-width: 120px;")
        self.combo_devices = QComboBox()
        self.btn_refresh_dev = QPushButton("🔄")
        self.btn_refresh_dev.setFixedWidth(40)
        self.btn_refresh_dev.setToolTip("Odśwież tylko listę mikrofonów")
        self.btn_refresh_dev.clicked.connect(self._refresh_microphones)
        mic_row.addWidget(self.lbl_mic_input)
        mic_row.addWidget(self.combo_devices, stretch=1)
        mic_row.addWidget(self.btn_refresh_dev)
        sources_layout.addLayout(mic_row)

        # 3. Wybór Wyjścia Loopback (Głośniki / Słuchawki)
        sys_row = QHBoxLayout()
        self.lbl_sys_input = QLabel("🎧 Dźwięk Systemu:")
        self.lbl_sys_input.setFont(QFont("Segoe UI", 9))
        self.lbl_sys_input.setStyleSheet("min-width: 120px;")
        self.combo_loopback_devices = QComboBox()
        self.btn_refresh_loop = QPushButton("🔄")
        self.btn_refresh_loop.setFixedWidth(40)
        self.btn_refresh_loop.setToolTip("Odśwież tylko urządzenia wyjściowe (Głośniki / Słuchawki)")
        self.btn_refresh_loop.clicked.connect(self._refresh_loopback_devices)
        sys_row.addWidget(self.lbl_sys_input)
        sys_row.addWidget(self.combo_loopback_devices, stretch=1)
        sys_row.addWidget(self.btn_refresh_loop)
        sources_layout.addLayout(sys_row)

        # 4. Wybór Konkretnej Aplikacji Audio (Discord, Firefox / YouTube, Teams itp.)
        app_row = QHBoxLayout()
        self.lbl_app_input = QLabel("🎯 Aplikacja audio:")
        self.lbl_app_input.setFont(QFont("Segoe UI", 9))
        self.lbl_app_input.setStyleSheet("min-width: 120px;")
        self.combo_target_apps = QComboBox()
        self.combo_target_apps.currentIndexChanged.connect(self._on_target_app_changed)
        self.btn_refresh_apps = QPushButton("🔄")
        self.btn_refresh_apps.setFixedWidth(40)
        self.btn_refresh_apps.setToolTip("Odśwież tylko listę aktywnych programów z dźwiękiem (np. Discord, Firefox, Chrome)")
        self.btn_refresh_apps.clicked.connect(self._refresh_target_apps)
        app_row.addWidget(self.lbl_app_input)
        app_row.addWidget(self.combo_target_apps, stretch=1)
        app_row.addWidget(self.btn_refresh_apps)
        sources_layout.addLayout(app_row)

        main_layout.addWidget(sources_box)

        # WYBÓR MODELU FASTER-WHISPER I AKCELERACJA SPRZĘTOWA
        model_box = QGroupBox("Model Transkrypcji AI (Faster-Whisper)")
        model_layout = QVBoxLayout(model_box)

        model_row = QHBoxLayout()
        lbl_model_prefix = QLabel("Wybierz model:")
        lbl_model_prefix.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        self.combo_models = QComboBox()
        
        for m_id, m_info in WHISPER_MODELS.items():
            self.combo_models.addItem(m_info["label"], userData=m_id)

        default_idx = self.combo_models.findData(DEFAULT_WHISPER_MODEL)
        if default_idx != -1:
            self.combo_models.setCurrentIndex(default_idx)

        self.combo_models.currentIndexChanged.connect(self._on_model_selection_changed)

        self.btn_auto_detect = QPushButton("🎯 Auto-dopasuj")
        self.btn_auto_detect.setToolTip("Automatycznie dopasuj model i parametry do parametrów Twojego komputera")
        self.btn_auto_detect.clicked.connect(self._on_auto_detect_clicked)

        model_row.addWidget(lbl_model_prefix)
        model_row.addWidget(self.combo_models, stretch=1)
        model_row.addWidget(self.btn_auto_detect)
        model_layout.addLayout(model_row)

        self.lbl_model_desc = QLabel(WHISPER_MODELS.get(DEFAULT_WHISPER_MODEL, {}).get("desc", ""))
        self.lbl_model_desc.setStyleSheet("color: #8d99ae; font-size: 11px; margin-top: 2px;")
        model_layout.addWidget(self.lbl_model_desc)

        hw_info = get_hardware_acceleration_info()
        self.lbl_hw_badge = QLabel(hw_info["badge_text"])
        self.lbl_hw_badge.setStyleSheet("color: #10b981; font-weight: bold; font-size: 11px; margin-top: 4px;")
        model_layout.addWidget(self.lbl_hw_badge)

        main_layout.addWidget(model_box)

        # PANEL MONITORINGU
        display_frame = QFrame()
        display_frame.setObjectName("DisplayFrame")
        display_layout = QVBoxLayout(display_frame)
        display_layout.setContentsMargins(18, 18, 18, 18)

        self.lbl_status_badge = QLabel("ZATRZYMANY")
        self.lbl_status_badge.setObjectName("StatusStopped")
        self.lbl_status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status_badge.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        display_layout.addWidget(self.lbl_status_badge, alignment=Qt.AlignmentFlag.AlignCenter)

        self.lbl_timer = QLabel("00:00:00")
        self.lbl_timer.setFont(QFont("Consolas", 36, QFont.Weight.Bold))
        self.lbl_timer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_timer.setMinimumHeight(50)
        self.lbl_timer.setStyleSheet("color: #edf2f4; margin: 4px 0;")
        display_layout.addWidget(self.lbl_timer)

        silence_header_layout = QHBoxLayout()
        self.lbl_silence_title = QLabel("Brak mowy (Auto-Pauza przy 5.0 s):")
        self.lbl_silence_title.setFont(QFont("Segoe UI", 9))
        self.lbl_silence_val = QLabel("0.0 s / 5.0 s")
        self.lbl_silence_val.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_silence_val.setStyleSheet("color: #f59e0b;")

        silence_header_layout.addWidget(self.lbl_silence_title)
        silence_header_layout.addStretch()
        silence_header_layout.addWidget(self.lbl_silence_val)
        display_layout.addLayout(silence_header_layout)

        self.progress_silence = QProgressBar()
        self.progress_silence.setRange(0, 50)
        self.progress_silence.setValue(0)
        self.progress_silence.setTextVisible(False)
        self.progress_silence.setFixedHeight(10)
        self.progress_silence.setObjectName("SilenceProgress")
        display_layout.addWidget(self.progress_silence)

        # PODWÓJNY WSKAŹNIK VU METER (Mikrofon + Dźwięk Systemu)
        vu_grid = QVBoxLayout()
        vu_grid.setSpacing(6)

        mic_vu_row = QHBoxLayout()
        self.lbl_vu_mic_title = QLabel("🎙️ Mikrofon:")
        self.lbl_vu_mic_title.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
        self.lbl_vu_mic_title.setStyleSheet("color: #4cc9f0; min-width: 120px;")
        self.progress_vu_mic = QProgressBar()
        self.progress_vu_mic.setRange(0, 100)
        self.progress_vu_mic.setValue(0)
        self.progress_vu_mic.setTextVisible(False)
        self.progress_vu_mic.setFixedHeight(8)
        self.progress_vu_mic.setStyleSheet("""
            QProgressBar {
                border: 1px solid #2b2d42;
                border-radius: 4px;
                background-color: #181824;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4cc9f0, stop:0.8 #4895ef, stop:1 #f72585);
                border-radius: 3px;
            }
        """)
        self.btn_mute_mic = QPushButton("🔊")
        self.btn_mute_mic.setFixedSize(36, 22)
        self.btn_mute_mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mute_mic.setToolTip("Wycisz mikrofon")
        self.btn_mute_mic.setStyleSheet("""
            QPushButton {
                background-color: #2b2d42;
                color: #edf2f4;
                border: 1px solid #4a4e69;
                border-radius: 4px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3d405b;
                border-color: #4cc9f0;
            }
        """)
        self.btn_mute_mic.clicked.connect(self._toggle_mic_mute)

        mic_vu_row.addWidget(self.lbl_vu_mic_title)
        mic_vu_row.addWidget(self.progress_vu_mic, stretch=1)
        mic_vu_row.addWidget(self.btn_mute_mic)
        vu_grid.addLayout(mic_vu_row)

        sys_vu_row = QHBoxLayout()
        self.lbl_vu_sys_title = QLabel("🎧 Dźwięk Systemu:")
        self.lbl_vu_sys_title.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
        self.lbl_vu_sys_title.setStyleSheet("color: #a370f7; min-width: 120px;")
        self.progress_vu_sys = QProgressBar()
        self.progress_vu_sys.setRange(0, 100)
        self.progress_vu_sys.setValue(0)
        self.progress_vu_sys.setTextVisible(False)
        self.progress_vu_sys.setFixedHeight(8)
        self.progress_vu_sys.setStyleSheet("""
            QProgressBar {
                border: 1px solid #2b2d42;
                border-radius: 4px;
                background-color: #181824;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a370f7, stop:0.8 #7209b7, stop:1 #f72585);
                border-radius: 3px;
            }
        """)
        self.btn_mute_sys = QPushButton("🔊")
        self.btn_mute_sys.setFixedSize(36, 22)
        self.btn_mute_sys.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mute_sys.setToolTip("Wycisz dźwięk systemu")
        self.btn_mute_sys.setStyleSheet("""
            QPushButton {
                background-color: #2b2d42;
                color: #edf2f4;
                border: 1px solid #4a4e69;
                border-radius: 4px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3d405b;
                border-color: #a370f7;
            }
        """)
        self.btn_mute_sys.clicked.connect(self._toggle_sys_mute)

        sys_vu_row.addWidget(self.lbl_vu_sys_title)
        sys_vu_row.addWidget(self.progress_vu_sys, stretch=1)
        sys_vu_row.addWidget(self.btn_mute_sys)
        vu_grid.addLayout(sys_vu_row)

        self.progress_vu = self.progress_vu_mic  # Kompatybilność
        display_layout.addLayout(vu_grid)

        self.lbl_vad_detail = QLabel("VAD: Oczekiwanie na uruchomienie...")
        self.lbl_vad_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_vad_detail.setStyleSheet("color: #8d99ae; font-size: 11px; margin-top: 4px;")
        display_layout.addWidget(self.lbl_vad_detail)

        main_layout.addWidget(display_frame)

        # SUWAK PROGU
        slider_box = QGroupBox("Ustawienia Automatycznego Wstrzymywania")
        slider_layout = QHBoxLayout(slider_box)

        lbl_thresh = QLabel("Próg braku mowy:")
        self.lbl_thresh_val = QLabel("5.0 s")
        self.lbl_thresh_val.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_thresh_val.setStyleSheet("color: #4cc9f0;")

        self.slider_silence = QSlider(Qt.Orientation.Horizontal)
        self.slider_silence.setRange(1, 10)
        self.slider_silence.setValue(5)
        self.slider_silence.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_silence.setTickInterval(1)
        self.slider_silence.valueChanged.connect(self._on_silence_slider_changed)

        slider_layout.addWidget(lbl_thresh)
        slider_layout.addWidget(self.slider_silence, stretch=1)
        slider_layout.addWidget(self.lbl_thresh_val)
        main_layout.addWidget(slider_box)

        # PRZYCISKI STEROWANIA
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(10)

        self.btn_start = QPushButton("⏺ Start Nagrywania")
        self.btn_start.setObjectName("BtnStart")
        self.btn_start.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.btn_start.setMinimumHeight(48)
        self.btn_start.clicked.connect(self._on_start_clicked)

        self.btn_pause = QPushButton("⏸ Wstrzymaj Ręcznie")
        self.btn_pause.setObjectName("BtnPause")
        self.btn_pause.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.btn_pause.setMinimumHeight(48)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._on_pause_clicked)

        self.btn_stop = QPushButton("⏹ Stop i Zapisz")
        self.btn_stop.setObjectName("BtnStop")
        self.btn_stop.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.btn_stop.setMinimumHeight(48)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop_clicked)

        self.btn_upload = QPushButton("📂 Prześlij Plik Audio")
        self.btn_upload.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.btn_upload.setMinimumHeight(48)
        self.btn_upload.setStyleSheet("background-color: #3a0ca3; color: #ffffff; border-radius: 8px; padding: 0 14px;")
        self.btn_upload.setToolTip("Wgraj gotowy plik audio (WAV, MP3, M4A, FLAC, OGG, AAC, MP4, MKV) do transkrypcji i diaryzacji")
        self.btn_upload.clicked.connect(self._on_upload_file_clicked)

        controls_layout.addWidget(self.btn_start, stretch=3)
        controls_layout.addWidget(self.btn_pause, stretch=2)
        controls_layout.addWidget(self.btn_stop, stretch=2)
        controls_layout.addWidget(self.btn_upload, stretch=3)
        main_layout.addLayout(controls_layout)

        # WYGENEROWANE WYJŚCIA (Master GroupBox)
        outputs_box = QGroupBox("Wygenerowane Wyjścia i Transkrypcje")
        outputs_main_layout = QVBoxLayout(outputs_box)

        # Opcje Diaryzacji i Liczby Osób
        diarization_row = QHBoxLayout()
        self.check_enable_diarization = QCheckBox("Włącz diaryzację mówców (PyAnnote AI - podział na osoby)")
        self.check_enable_diarization.setChecked(False)
        self.check_enable_diarization.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        self.check_enable_diarization.toggled.connect(self._on_diarization_toggled)

        lbl_speakers = QLabel("Liczba osób:")
        lbl_speakers.setStyleSheet("color: #8d99ae; font-size: 11px;")
        self.combo_speakers = QComboBox()
        for label, count_val in SPEAKER_COUNT_OPTIONS:
            self.combo_speakers.addItem(label, userData=count_val)

        diarization_row.addWidget(self.check_enable_diarization, stretch=1)
        diarization_row.addWidget(lbl_speakers)
        diarization_row.addWidget(self.combo_speakers)
        outputs_main_layout.addLayout(diarization_row)

        # Token + Pasek Postępu AI
        token_layout = QHBoxLayout()
        lbl_token = QLabel("HuggingFace Token:")
        self.input_token = QLineEdit()
        self.input_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_token.setPlaceholderText("Wklej tutaj token HuggingFace (hf_...) - wymagany tylko do diaryzacji")
        
        loaded_token = get_hf_token()
        if loaded_token:
            self.input_token.setText(loaded_token)

        token_layout.addWidget(lbl_token)
        token_layout.addWidget(self.input_token)
        outputs_main_layout.addLayout(token_layout)

        self.progress_transcription = QProgressBar()
        self.progress_transcription.setObjectName("TranscriptionProgress")
        self.progress_transcription.setRange(0, 100)
        self.progress_transcription.setValue(0)
        self.progress_transcription.setTextVisible(True)
        self.progress_transcription.setFixedHeight(22)
        self.progress_transcription.setFormat("Oczekiwanie na nagranie lub plik...")
        outputs_main_layout.addWidget(self.progress_transcription)

        # UKŁAD DWUKOLUMNOWY: LEWA = NAGRANIA AUDIO, PRAWA = TRANSKRYPCJE TEKSTOWE
        columns_layout = QHBoxLayout()

        # LEWA KOLUMNA: NAGRANIA AUDIO (.wav)
        left_box = QGroupBox("🎵 Nagrania Audio (.wav)")
        left_layout = QVBoxLayout(left_box)

        self.lbl_path_audio = QLabel(f"Folder: {self.recordings_dir}")
        self.lbl_path_audio.setStyleSheet("color: #8d99ae; font-size: 11px;")
        left_layout.addWidget(self.lbl_path_audio)

        self.list_recordings = QListWidget()
        self.list_recordings.setFixedHeight(110)
        self.list_recordings.itemDoubleClicked.connect(self._on_recording_double_clicked)
        left_layout.addWidget(self.list_recordings)

        btn_open_audio_folder = QPushButton("📁 Otwórz folder nagrań")
        btn_open_audio_folder.clicked.connect(self._on_open_folder_clicked)
        left_layout.addWidget(btn_open_audio_folder)

        columns_layout.addWidget(left_box, stretch=1)

        # PRAWA KOLUMNA: TRANSKRYPCJE TEKSTOWE (.txt)
        right_box = QGroupBox("📄 Transkrypcje Tekstowe (.txt)")
        right_layout = QVBoxLayout(right_box)

        self.lbl_path_txt = QLabel(f"Folder: {self.transcriptions_dir}")
        self.lbl_path_txt.setStyleSheet("color: #8d99ae; font-size: 11px;")
        right_layout.addWidget(self.lbl_path_txt)

        self.list_transcriptions = QListWidget()
        self.list_transcriptions.setFixedHeight(110)
        self.list_transcriptions.itemDoubleClicked.connect(self._on_transcription_double_clicked)
        right_layout.addWidget(self.list_transcriptions)

        txt_actions_layout = QHBoxLayout()
        txt_actions_layout.setSpacing(6)

        btn_open_txt_folder = QPushButton("📁 Folder")
        btn_open_txt_folder.setFixedHeight(32)
        btn_open_txt_folder.clicked.connect(self._on_open_txt_folder_clicked)

        self.btn_run_diarization = QPushButton("👥 Rozpoznaj Mówców (PyAnnote)")
        self.btn_run_diarization.setFixedHeight(32)
        self.btn_run_diarization.setStyleSheet("background-color: #7209b7; color: #ffffff; font-weight: bold; border-radius: 4px; padding: 0 10px;")
        self.btn_run_diarization.setToolTip("Uruchamia analizę mówców PyAnnote w tle dla zaznaczonego nagrania bez ponownej transkrypcji Whispera")
        self.btn_run_diarization.clicked.connect(self._on_run_diarization_clicked)

        txt_actions_layout.addWidget(btn_open_txt_folder, stretch=1)
        txt_actions_layout.addWidget(self.btn_run_diarization, stretch=2)
        right_layout.addLayout(txt_actions_layout)

        columns_layout.addWidget(right_box, stretch=1)

        outputs_main_layout.addLayout(columns_layout)

        # PANEL MAPOWANIA I WERYFIKACJI MÓWCÓW
        self.speaker_box = QGroupBox("👥 Przypisanie i Korekta Mówców (Weryfikacja)")
        self.speaker_box.setStyleSheet("QGroupBox { border: 1px solid #4361ee; margin-top: 10px; font-weight: bold; }")
        speaker_main_layout = QVBoxLayout(self.speaker_box)
        speaker_main_layout.setContentsMargins(12, 14, 12, 12)
        speaker_main_layout.setSpacing(10)

        lbl_spk_info = QLabel("🤖 Program przeanalizował dialogi i zasugerował imiona. Zweryfikuj je lub popraw przed zapisem:")
        lbl_spk_info.setStyleSheet("color: #4cc9f0; font-size: 11px;")
        speaker_main_layout.addWidget(lbl_spk_info)

        # Przewijalny obszar dla mówców (zapewnia doskonałą widoczność dla 3-6 osób jednocześnie)
        speaker_scroll = QScrollArea()
        speaker_scroll.setWidgetResizable(True)
        speaker_scroll.setFrameShape(QFrame.Shape.NoFrame)
        speaker_scroll.setMinimumHeight(160)
        speaker_scroll.setMaximumHeight(320)
        speaker_scroll.setStyleSheet("background: transparent;")

        speaker_scroll_widget = QWidget()
        speaker_scroll_widget.setStyleSheet("background: transparent;")
        self.speaker_rows_layout = QVBoxLayout(speaker_scroll_widget)
        self.speaker_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.speaker_rows_layout.setSpacing(8)
        speaker_scroll.setWidget(speaker_scroll_widget)

        speaker_main_layout.addWidget(speaker_scroll)

        self.btn_apply_speakers = QPushButton("✅ Zastosuj Imiona Mówców i Zapisz Zmiany")
        self.btn_apply_speakers.setFixedHeight(36)
        self.btn_apply_speakers.setStyleSheet("background-color: #2b9348; color: #ffffff; font-weight: bold; border-radius: 6px;")
        self.btn_apply_speakers.clicked.connect(self._on_apply_speakers_clicked)
        speaker_main_layout.addWidget(self.btn_apply_speakers)

        self.speaker_box.setVisible(False)
        outputs_main_layout.addWidget(self.speaker_box)

        # PODGLĄD AKTYWNEJ TRANSKRYPCJI
        self.text_transcript = QTextEdit()
        self.text_transcript.setReadOnly(True)
        self.text_transcript.setMinimumHeight(220)
        self.text_transcript.setPlaceholderText("Tutaj pojawi się transkrypcja z podziałem na role po zakończeniu nagrywania / wgraniu pliku...")
        outputs_main_layout.addWidget(self.text_transcript)

        # PRZYCISKI KOPIOWANIA I POBIERANIA TRANSKRYPCJI
        transcript_actions_layout = QHBoxLayout()
        transcript_actions_layout.setSpacing(8)

        self.btn_copy_transcript = QPushButton("📋 Kopiuj transkrypcję")
        self.btn_copy_transcript.setFixedHeight(32)
        self.btn_copy_transcript.setStyleSheet(
            "background-color: #2b2d42; color: #4cc9f0; border: 1px solid #3d405b; "
            "border-radius: 6px; font-weight: bold; padding: 0 14px;"
        )
        self.btn_copy_transcript.setToolTip("Kopiuj całą transkrypcję do schowka")
        self.btn_copy_transcript.clicked.connect(self._on_copy_transcript_clicked)

        self.btn_save_transcript = QPushButton("💾 Pobierz .txt")
        self.btn_save_transcript.setFixedHeight(32)
        self.btn_save_transcript.setStyleSheet(
            "background-color: #2b2d42; color: #10b981; border: 1px solid #3d405b; "
            "border-radius: 6px; font-weight: bold; padding: 0 14px;"
        )
        self.btn_save_transcript.setToolTip("Zapisz transkrypcję jako plik .txt w wybranej lokalizacji")
        self.btn_save_transcript.clicked.connect(self._on_save_transcript_clicked)

        transcript_actions_layout.addStretch()
        transcript_actions_layout.addWidget(self.btn_copy_transcript)
        transcript_actions_layout.addWidget(self.btn_save_transcript)
        outputs_main_layout.addLayout(transcript_actions_layout)


        # PASEK SYNCHRONIZACJI CHMUROWEJ (CLOUD SYNC / EMANAGER.PRO / CRM)
        cloud_bar_layout = QHBoxLayout()
        cloud_bar_layout.setContentsMargins(4, 4, 4, 4)

        sync_target_name = self.cloud_sync.config.get("sync_target", "emanager").upper()
        self.lbl_cloud_status = QLabel(f"☁️ Integracja: {sync_target_name} (Gotowa)")
        self.lbl_cloud_status.setStyleSheet("color: #4cc9f0; font-size: 11px; font-weight: bold;")

        self.btn_manual_sync = QPushButton(f"☁️ Wyślij do {sync_target_name}")
        self.btn_manual_sync.setFixedHeight(30)
        self.btn_manual_sync.setStyleSheet("background-color: #4361ee; color: #ffffff; font-weight: bold; border-radius: 4px; padding: 0 12px;")
        self.btn_manual_sync.setEnabled(False)
        self.btn_manual_sync.clicked.connect(self._on_manual_sync_clicked)

        cloud_bar_layout.addWidget(self.lbl_cloud_status, stretch=1)
        cloud_bar_layout.addWidget(self.btn_manual_sync)
        outputs_main_layout.addLayout(cloud_bar_layout)

        main_layout.addWidget(outputs_box)


    def _open_settings_dialog(self, initial_tab=None):
        """Otwiera okno konfiguracji słownika branżowego, parametrów AI i chmury."""
        dlg = SettingsDialog(self)
        if initial_tab is not None:
            dlg.select_tab(initial_tab)
        if dlg.exec():
            st = load_user_settings()
            # 1. Aktualizacja tokenu HF w polu UI jeśli został zmieniony
            new_token = st.get("hf_token", "").strip()
            if new_token:
                self.input_token.setText(new_token)

            # 2. Aktualizacja czułości VAD w aktywnym detektorze
            new_vad = float(st.get("vad_speech_threshold", 0.35))
            if hasattr(self, "worker") and getattr(self.worker, "vad_detector", None):
                self.worker.vad_detector.speech_threshold = new_vad

            # 3. Aktualizacja czasu auto-pauzy
            new_pause = int(float(st.get("auto_pause_sec", 5.0)))
            self.slider_silence.setValue(new_pause)
            if hasattr(self, "worker"):
                self.worker.set_auto_pause_sec(float(new_pause))
                self.worker.set_silence_alert_seconds(get_silence_alert_seconds())
                self.worker.set_session_split_silence_sec(get_session_split_silence_sec())

            # 4. Natychmiastowe odświeżenie widoku podglądu transkrypcji (kolejność / format)
            self._refresh_current_transcript_view()

            QMessageBox.information(
                self,
                "Ustawienia Zapisane",
                "Ustawienia zostały pomyślnie zaktualizowane!\n\n"
                "Nowy słownik branżowy oraz parametry AI będą automatycznie stosowane przy kolejnych nagraniach i transkrypcjach."
            )

    def _start_silent_update_check(self):
        """Cicho sprawdza w tle na GitHubie dostępność nowszej wersji programu."""
        try:
            from recorder.core.updater import CheckUpdateWorker
            st = load_user_settings()
            inc_pre = bool(st.get("check_prereleases", True))
            self._startup_update_worker = CheckUpdateWorker(include_prereleases=inc_pre)
            self._active_threads.append(self._startup_update_worker)
            self._startup_update_worker.update_checked_signal.connect(self._on_startup_update_result)
            self._startup_update_worker.finished.connect(
                lambda: self._active_threads.remove(self._startup_update_worker) if hasattr(self, "_active_threads") and hasattr(self, "_startup_update_worker") and self._startup_update_worker in self._active_threads else None
            )
            self._startup_update_worker.start()
        except Exception as e:
            print(f"[UPDATER] Ciche sprawdzenie aktualizacji pominięte: {e}")

    def _on_startup_update_result(self, result):
        """Obsługuje wynik cichego sprawdzania aktualizacji przy starcie."""
        if result and result.get("has_update"):
            latest_v = result.get("latest_version", "")
            self.lbl_update_banner_text.setText(f"🚀 Dostępna jest nowa wersja dyktafonu: <b>{latest_v}</b>")
            self.banner_update.show()

    def set_pending_update(self, zip_path: str, version: str):
        """Ustawia paczkę aktualizacji do zainstalowania przy zamknięciu programu."""
        self._pending_update_zip_path = zip_path
        self._pending_update_version = version

    def _scroll_transcript_view(self):
        """Automatycznie ustawia pozycję paska przewijania w oknie transkrypcji."""
        from recorder.config import get_preview_order, is_auto_scroll_chronological
        order = get_preview_order()
        auto_scroll = is_auto_scroll_chronological()

        def do_scroll():
            sb = self.text_transcript.verticalScrollBar()
            if order == "chronological":
                if auto_scroll:
                    sb.setValue(sb.maximum())
            else:
                sb.setValue(0)

        do_scroll()
        QTimer.singleShot(25, do_scroll)

    def _refresh_current_transcript_view(self):
        """Odświeża wyświetlanie bieżącej transkrypcji zgodnie z aktualną konfiguracją (kolejność, timestampy)."""
        if not self.current_turns:
            return

        session_dt = getattr(self, "session_start_time", None) or getattr(self, "current_session_start_time", None)
        if not session_dt and self.current_txt_path:
            session_dt = extract_datetime_from_filename(self.current_txt_path)
        if not session_dt and self.last_audio_save_path:
            session_dt = extract_datetime_from_filename(self.last_audio_save_path)

        if self.current_txt_path:
            json_path = get_session_path_for_txt(self.current_txt_path)
            if os.path.exists(json_path):
                sess = TranscriptionSession.load_from_json(json_path)
                if sess and sess.turns:
                    html_content = sess.export_to_html(session_start_time=session_dt)
                    self.text_transcript.setHtml(html_content)
                    self._scroll_transcript_view()
                    return

        mapping = {}
        for spk_id, fields in self.speaker_inputs.items():
            if isinstance(fields, dict):
                n = fields["name"].text().strip()
                r = fields["role"].text().strip()
                if n and r:
                    mapping[spk_id] = f"{n} ({r})"
                elif n:
                    mapping[spk_id] = n
                elif r:
                    mapping[spk_id] = f"{spk_id} ({r})"
            elif hasattr(fields, "text"):
                val = fields.text().strip()
                if val:
                    mapping[spk_id] = val

        html_content, _ = format_turns(self.current_turns, mapping, session_start_time=session_dt)
        self.text_transcript.setHtml(html_content)
        self._scroll_transcript_view()

    def _on_copy_transcript_clicked(self):
        """Kopiuje bieżącą transkrypcję do schowka systemowego."""
        text = self.last_plain_text or self.text_transcript.toPlainText()
        if not text or not text.strip():
            QMessageBox.information(self, "Brak transkrypcji", "Nie ma jeszcze żadnej transkrypcji do skopiowania.")
            return
        QApplication.clipboard().setText(text)
        # Krótki feedback na etykiecie przycisku
        self.btn_copy_transcript.setText("✅ Skopiowano!")
        QTimer.singleShot(2000, lambda: self.btn_copy_transcript.setText("📋 Kopiuj transkrypcję"))

    def _on_save_transcript_clicked(self):
        """Otwiera dialog 'Zapisz jako' i eksportuje transkrypcję do wybranego pliku .txt."""
        text = self.last_plain_text or self.text_transcript.toPlainText()
        if not text or not text.strip():
            QMessageBox.information(self, "Brak transkrypcji", "Nie ma jeszcze żadnej transkrypcji do zapisania.")
            return

        # Propozycja nazwy pliku na podstawie bieżącego timestampu lub aktualnej transkrypcji
        if hasattr(self, "current_live_timestamp") and self.current_live_timestamp:
            default_name = f"transkrypcja_{self.current_live_timestamp}.txt"
        elif self.current_txt_path:
            default_name = os.path.basename(self.current_txt_path)
        else:
            default_name = f"transkrypcja_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Zapisz transkrypcję jako...",
            os.path.join(os.path.expanduser("~"), "Desktop", default_name),
            "Plik tekstowy (*.txt);;Wszystkie pliki (*.*)"
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            self.btn_save_transcript.setText("✅ Zapisano!")
            QTimer.singleShot(2000, lambda: self.btn_save_transcript.setText("💾 Pobierz .txt"))
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", f"Nie udało się zapisać pliku:\n{e}")

    def _apply_theme(self):

        app = QApplication.instance()
        if app:
            setup_dark_palette(app)
            app.setStyleSheet(DARK_THEME_QSS)
        self.setStyleSheet(DARK_THEME_QSS)

    def _refresh_microphones(self):
        """Odświeża wyłącznie listę mikrofonów wejściowych."""
        current_data = self.combo_devices.currentData()
        self.combo_devices.clear()
        devices = get_working_input_devices(force_refresh=False)
        if devices:
            default_idx = 0
            for i, dev in enumerate(devices):
                label = f"{dev['name']} ({dev['hostapi']})"
                self.combo_devices.addItem(label, userData=dev['index'])
                if current_data is not None and dev['index'] == current_data:
                    default_idx = i
            self.combo_devices.setCurrentIndex(default_idx)
        else:
            self.combo_devices.addItem("⚠️ BRAK MIKROFONU", userData=None)

    def _refresh_loopback_devices(self):
        """Odświeża wyłącznie listę urządzeń wyjściowych / loopback (głośniki/słuchawki)."""
        current_data = self.combo_loopback_devices.currentData()
        self.combo_loopback_devices.clear()
        loopbacks = get_working_loopback_devices()
        if loopbacks:
            default_idx = 0
            for i, loop_dev in enumerate(loopbacks):
                self.combo_loopback_devices.addItem(loop_dev['label'], userData=loop_dev['index'])
                if current_data is not None and loop_dev['index'] == current_data:
                    default_idx = i
                elif loop_dev.get('is_default') and current_data is None:
                    default_idx = i
            self.combo_loopback_devices.setCurrentIndex(default_idx)
        else:
            try:
                import pyaudiowpatch as pyaudio
                p_tmp = pyaudio.PyAudio()
                def_l = p_tmp.get_default_wasapi_loopback()
                p_tmp.terminate()
                if def_l:
                    c_name = def_l.get('name', 'Głośniki systemowe').replace(" [Loopback]", "").strip()
                    self.combo_loopback_devices.addItem(f"🎧 {c_name} (Domyślne)", userData=def_l.get('index'))
                else:
                    self.combo_loopback_devices.addItem("🎧 Domyślne wyjście systemowe", userData=None)
            except Exception:
                self.combo_loopback_devices.addItem("🎧 Domyślne wyjście systemowe", userData=None)

    def _refresh_target_apps(self):
        """Odświeża wyłącznie listę aktywnych programów z dźwiękiem (np. Discord, Firefox, Chrome)."""
        current_exe = self.combo_target_apps.currentData()
        self.combo_target_apps.blockSignals(True)
        self.combo_target_apps.clear()
        self.combo_target_apps.addItem("Wszystkie programy (cały mikser)", userData="")
        try:
            active_apps = get_active_audio_apps()
            if active_apps:
                match_idx = 0
                for i, app_info in enumerate(active_apps, start=1):
                    label = f"{app_info['name']} ({app_info['exe']})"
                    self.combo_target_apps.addItem(label, userData=app_info['exe'])
                    if current_exe and app_info['exe'].lower() == current_exe.lower():
                        match_idx = i
                self.combo_target_apps.setCurrentIndex(match_idx)
        except Exception:
            pass
        finally:
            self.combo_target_apps.blockSignals(False)

    def _on_target_app_changed(self):
        """Dynamicznie aktualizuje filtr wybranej aplikacji audio w locie."""
        new_filter = self.combo_target_apps.currentData() or ""
        if hasattr(self, "worker") and self.worker is not None and self.worker.state != SmartRecordState.STOPPED:
            self.worker.update_target_app_filter(new_filter)
            app_text = self.combo_target_apps.currentText()
            self.lbl_cloud_status.setText(f"🎯 Przełączono nasłuch w locie: {app_text}")
            self.lbl_cloud_status.setStyleSheet("color: #a370f7; font-size: 11px; font-weight: bold;")

    def _refresh_audio_devices(self):
        """Pełne odświeżenie wszystkich źródeł dźwięku."""
        self._refresh_microphones()
        self._refresh_loopback_devices()
        self._refresh_target_apps()
        self._on_source_mode_changed()

    def _on_source_mode_changed(self):
        """Dopasowuje dostępność kontrolek i wskaźników VU do wybranego trybu źródła."""
        mode = self.combo_source_mode.currentData() or RecordSourceMode.HYBRID_DUAL
        is_recording = (hasattr(self, "worker") and self.worker is not None and self.worker.state != SmartRecordState.STOPPED)

        if mode == RecordSourceMode.MIC_ONLY:
            self.combo_devices.setEnabled(not is_recording)
            self.btn_refresh_dev.setEnabled(not is_recording)
            self.combo_loopback_devices.setEnabled(False)
            self.btn_refresh_loop.setEnabled(False)
            self.combo_target_apps.setEnabled(False)
            self.btn_refresh_apps.setEnabled(False)
            self.lbl_mic_input.setStyleSheet("color: #4cc9f0; min-width: 120px; font-weight: bold;")
            self.lbl_sys_input.setStyleSheet("color: #8d99ae; min-width: 120px;")
            self.lbl_app_input.setStyleSheet("color: #8d99ae; min-width: 120px;")
            self.lbl_vu_mic_title.setVisible(True)
            self.progress_vu_mic.setVisible(True)
            self.btn_mute_mic.setVisible(True)
            self.lbl_vu_sys_title.setVisible(False)
            self.progress_vu_sys.setVisible(False)
            self.btn_mute_sys.setVisible(False)
        elif mode == RecordSourceMode.SYSTEM_ONLY:
            self.combo_devices.setEnabled(False)
            self.btn_refresh_dev.setEnabled(False)
            self.combo_loopback_devices.setEnabled(not is_recording)
            self.btn_refresh_loop.setEnabled(not is_recording)
            self.combo_target_apps.setEnabled(True)
            self.btn_refresh_apps.setEnabled(True)
            self.lbl_mic_input.setStyleSheet("color: #8d99ae; min-width: 120px;")
            self.lbl_sys_input.setStyleSheet("color: #a370f7; min-width: 120px; font-weight: bold;")
            self.lbl_app_input.setStyleSheet("color: #a370f7; min-width: 120px; font-weight: bold;")
            self.lbl_vu_mic_title.setVisible(False)
            self.progress_vu_mic.setVisible(False)
            self.btn_mute_mic.setVisible(False)
            self.lbl_vu_sys_title.setVisible(True)
            self.progress_vu_sys.setVisible(True)
            self.btn_mute_sys.setVisible(True)
        else:  # HYBRID_DUAL
            self.combo_devices.setEnabled(not is_recording)
            self.btn_refresh_dev.setEnabled(not is_recording)
            self.combo_loopback_devices.setEnabled(not is_recording)
            self.btn_refresh_loop.setEnabled(not is_recording)
            self.combo_target_apps.setEnabled(True)
            self.btn_refresh_apps.setEnabled(True)
            self.lbl_mic_input.setStyleSheet("color: #4cc9f0; min-width: 120px; font-weight: bold;")
            self.lbl_sys_input.setStyleSheet("color: #a370f7; min-width: 120px; font-weight: bold;")
            self.lbl_app_input.setStyleSheet("color: #a370f7; min-width: 120px; font-weight: bold;")
            self.lbl_vu_mic_title.setVisible(True)
            self.progress_vu_mic.setVisible(True)
            self.btn_mute_mic.setVisible(True)
            self.lbl_vu_sys_title.setVisible(True)
            self.progress_vu_sys.setVisible(True)
            self.btn_mute_sys.setVisible(True)

        if is_recording:
            self.combo_source_mode.setEnabled(False)
            self.combo_devices.setToolTip("Fizyczny mikrofon można zmienić przed lub po zakończeniu nagrania.")
            self.combo_loopback_devices.setToolTip("Fizyczne urządzenie wyjściowe można zmienić przed lub po zakończeniu nagrania.")
            self.combo_target_apps.setToolTip("Aplikację audio możesz w dowolnym momencie przełączyć w locie bez zatrzymywania nagrania!")
        else:
            self.combo_devices.setToolTip("")
            self.combo_loopback_devices.setToolTip("")
            self.combo_target_apps.setToolTip("Wybierz aplikację, z której dźwięk ma być rejestrowany.")

    def _toggle_mic_mute(self):
        """Wycisza lub przywraca nasłuch z mikrofonu w locie."""
        new_state = not getattr(self, "_mic_is_muted", False)
        self._mic_is_muted = new_state
        if new_state:
            self.btn_mute_mic.setText("🔇")
            self.btn_mute_mic.setToolTip("Włącz mikrofon")
            self.btn_mute_mic.setStyleSheet("""
                QPushButton {
                    background-color: #ef4444;
                    color: #ffffff;
                    border: 1px solid #dc2626;
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #dc2626;
                }
            """)
            self.lbl_vu_mic_title.setText("🎙️ Mikrofon (Wyciszony):")
            self.progress_vu_mic.setValue(0)
            if hasattr(self, "worker") and self.worker is not None:
                self.worker.set_mic_muted(True)
            self.lbl_cloud_status.setText("🔇 Wyciszono mikrofon.")
            self.lbl_cloud_status.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold;")
        else:
            self.btn_mute_mic.setText("🔊")
            self.btn_mute_mic.setToolTip("Wycisz mikrofon")
            self.btn_mute_mic.setStyleSheet("""
                QPushButton {
                    background-color: #2b2d42;
                    color: #edf2f4;
                    border: 1px solid #4a4e69;
                    border-radius: 4px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #3d405b;
                    border-color: #4cc9f0;
                }
            """)
            self.lbl_vu_mic_title.setText("🎙️ Mikrofon:")
            if hasattr(self, "worker") and self.worker is not None:
                self.worker.set_mic_muted(False)
            self.lbl_cloud_status.setText("🎙️ Włączono mikrofon.")
            self.lbl_cloud_status.setStyleSheet("color: #4cc9f0; font-size: 11px; font-weight: bold;")

    def _toggle_sys_mute(self):
        """Wycisza lub przywraca nasłuch dźwięku systemu w locie."""
        new_state = not getattr(self, "_sys_is_muted", False)
        self._sys_is_muted = new_state
        if new_state:
            self.btn_mute_sys.setText("🔇")
            self.btn_mute_sys.setToolTip("Włącz dźwięk systemu")
            self.btn_mute_sys.setStyleSheet("""
                QPushButton {
                    background-color: #ef4444;
                    color: #ffffff;
                    border: 1px solid #dc2626;
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #dc2626;
                }
            """)
            self.lbl_vu_sys_title.setText("🎧 Dźwięk Systemu (Wyciszony):")
            self.progress_vu_sys.setValue(0)
            if hasattr(self, "worker") and self.worker is not None:
                self.worker.set_sys_muted(True)
            self.lbl_cloud_status.setText("🔇 Wyciszono dźwięk systemu.")
            self.lbl_cloud_status.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold;")
        else:
            self.btn_mute_sys.setText("🔊")
            self.btn_mute_sys.setToolTip("Wycisz dźwięk systemu")
            self.btn_mute_sys.setStyleSheet("""
                QPushButton {
                    background-color: #2b2d42;
                    color: #edf2f4;
                    border: 1px solid #4a4e69;
                    border-radius: 4px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #3d405b;
                    border-color: #a370f7;
                }
            """)
            self.lbl_vu_sys_title.setText("🎧 Dźwięk Systemu:")
            if hasattr(self, "worker") and self.worker is not None:
                self.worker.set_sys_muted(False)
            self.lbl_cloud_status.setText("🎧 Włączono dźwięk systemu.")
            self.lbl_cloud_status.setStyleSheet("color: #a370f7; font-size: 11px; font-weight: bold;")

    def _update_dual_audio_level(self, mic_lvl: float, sys_lvl: float):
        """Aktualizacja podwójnego wskaźnika poziomu głośności VU meter w UI."""
        try:
            m_val = 0 if getattr(self, "_mic_is_muted", False) else int(max(0, min(100, mic_lvl)))
            s_val = 0 if getattr(self, "_sys_is_muted", False) else int(max(0, min(100, sys_lvl)))
            self.progress_vu_mic.setValue(m_val)
            self.progress_vu_sys.setValue(s_val)
        except Exception:
            pass

    def _refresh_recordings_list(self):
        """Odświeża listę nagrań WAV posortowaną chronologicznie (najnowsze na samej górze)."""
        self.list_recordings.clear()
        if not os.path.exists(self.recordings_dir):
            return

        full_paths = [os.path.join(self.recordings_dir, f) for f in os.listdir(self.recordings_dir) if f.endswith(".wav")]
        full_paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        for full_path in full_paths:
            filename = os.path.basename(full_path)
            size_kb = os.path.getsize(full_path) / 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%Y-%m-%d %H:%M:%S")
            
            item = QListWidgetItem(f"🎵 {filename}  ({size_kb:.1f} KB, {mtime})")
            item.setData(Qt.ItemDataRole.UserRole, full_path)
            self.list_recordings.addItem(item)

    def _on_model_selection_changed(self, index):
        model_id = self.combo_models.currentData()
        if model_id in WHISPER_MODELS:
            self.lbl_model_desc.setText(WHISPER_MODELS[model_id]["desc"])

    def _on_auto_detect_clicked(self):
        profile = get_recommended_profile()
        rec_model = profile["recommended_model"]
        idx = self.combo_models.findData(rec_model)
        if idx != -1:
            self.combo_models.setCurrentIndex(idx)
        QMessageBox.information(self, profile["title"], profile["message"])

    def _on_diarization_toggled(self, checked):
        self.input_token.setEnabled(checked)
        self.combo_speakers.setEnabled(checked)

    def _on_silence_slider_changed(self, value):
        self.lbl_thresh_val.setText(f"{value}.0 s")
        self.lbl_silence_title.setText(f"Brak mowy (Auto-Pauza przy {value}.0 s):")
        self.lbl_silence_val.setText(f"0.0 s / {value}.0 s")
        self.progress_silence.setRange(0, value * 10)
        self.worker.set_auto_pause_sec(value)

    def _on_start_clicked(self):
        selected_mode = self.combo_source_mode.currentData() or RecordSourceMode.HYBRID_DUAL
        selected_mic = self.combo_devices.currentData()
        selected_loopback = self.combo_loopback_devices.currentData()

        if selected_mode == RecordSourceMode.MIC_ONLY and selected_mic is None:
            QMessageBox.warning(
                self,
                "Brak Mikrofonu",
                "W systemie Windows nie wykryto aktywnego mikrofonu.\n\n"
                "Upewnij się, że mikrofon jest podłączony i włączony w:\n"
                "Ustawienia Windows -> System -> Dźwięk (Wejście),\n"
                "a następnie kliknij przycisk 🔄 Odśwież."
            )
            return

        # Guard: nie pozwól na start jeśli poprzednia sesja jeszcze nie zakończyła finalizacji
        if self._finalize_pending:
            QMessageBox.information(
                self,
                "Finalizacja w toku",
                "Poprzednie nagranie jest jeszcze finalizowane (zapis do chmury).\n"
                "Poczekaj chwilę i spróbuj ponownie."
            )
            return

        self.recorded_seconds = 0
        self._active_recorded_time = 0.0
        self._last_active_tick = None
        self.last_processed_block_idx = 0
        self.lbl_timer.setText("00:00:00")

        self.live_plain_text_lines = []
        self.text_transcript.clear()
        self.text_transcript.setPlaceholderText("Transkrypcja na żywo: Wypowiedzi będą pojawiać się tutaj automatycznie...")
        self.progress_transcription.setValue(0)
        self.progress_transcription.setFormat("Inicjalizacja transkrypcji na żywo...")

        # Timestamp z mikrosekundami — zapobiega kolizji UUID5 przy szybkim Stop→Start w tej samej sekundzie
        now = datetime.now()
        self.session_start_time = now
        timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
        self.current_live_timestamp = timestamp
        self.current_live_txt_path = os.path.join(self.transcriptions_dir, f"transkrypcja_{timestamp}.txt")
        self.current_live_wav_path = os.path.join(self.recordings_dir, f"inteligentne_nagranie_{timestamp}.wav")
        self.synced_segment_count = 0
        self._synced_turn_ids = set()
        try:
            with open(self.current_live_txt_path, 'w', encoding='utf-8') as f:
                f.write(f"=== TRANSKRYPCJA NA ŻYWO (Start: {now.strftime('%Y-%m-%d %H:%M:%S')}) ===\n\n")
            self._refresh_transcriptions_list()
        except Exception:
            pass

        selected_model = self.combo_models.currentData() or DEFAULT_WHISPER_MODEL

        # Inicjalizacja sesji w Supabase dla transmisji na żywo do CRM
        if self.cloud_sync.config.get("live_streaming") and self.cloud_sync.config.get("auto_sync"):
            self.current_meeting_id = self.cloud_sync.start_live_session_async(
                title=f"Spotkanie biurowe {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
            self.lbl_cloud_status.setText(f"🟢 Transmisja na żywo do {target_name} aktywna...")
            self.lbl_cloud_status.setStyleSheet("color: #4cc9f0; font-size: 11px; font-weight: bold;")

        mode_text = self.combo_source_mode.currentText() if hasattr(self, "combo_source_mode") else "N/A"
        mode_data = self.combo_source_mode.currentData() if hasattr(self, "combo_source_mode") else "N/A"
        mic_text = self.combo_devices.currentText() if hasattr(self, "combo_devices") else "N/A"
        sys_text = self.combo_loopback_devices.currentText() if hasattr(self, "combo_loopback_devices") else "N/A"
        app_text = self.combo_target_apps.currentText() if hasattr(self, "combo_target_apps") else "N/A"
        logger.info(
            f"[SESJA START] Rozpoczęto nagrywanie: tryb='{mode_text}' ({mode_data}), "
            f"mikrofon='{mic_text}', loopback='{sys_text}', aplikacja='{app_text}', "
            f"model='{selected_model}', meeting_id='{self.current_meeting_id}'"
        )

        # Ustawienie estetycznego komunikatu oczekiwania na pierwszy zweryfikowany blok mowy
        self.text_transcript.setHtml(
            "<div style='color: #4cc9f0; font-size: 13px; padding: 10px;'>"
            "🎙️ <b>Trwa inteligentne nagrywanie spotkania (Mikrofon + Słuchawki / Discord)...</b><br>"
            "<span style='color: #94a3b8; font-size: 11px;'>"
            "Mowa z biura oraz dźwięk ze spotkania online są na bieżąco analizowane dwutorowo przez Silero VAD i Faster-Whisper. "
            "Zweryfikowane wypowiedzi pojawią się automatycznie z podziałem na role."
            "</span></div>"
        )

        # Zabezpieczenie: zatrzymanie i wyczyszczenie poprzedniego wątku rolling_worker
        if getattr(self, "rolling_worker", None) is not None:
            try:
                self.rolling_worker.blockSignals(True)
                if self.rolling_worker.isRunning():
                    self.rolling_worker.stop()
                    self.rolling_worker.wait(1500)
                if self.rolling_worker in self._active_threads:
                    self._active_threads.remove(self.rolling_worker)
            except Exception:
                pass

        # Uruchomienie silnika asynchronicznego przetwarzania bloków w tle (Rolling Background Transcriber)
        self.rolling_worker = RollingTranscriptionWorker(
            model_size=selected_model,
            txt_save_path=self.current_live_txt_path,
            session_start_time=self.session_start_time
        )
        self._active_threads.append(self.rolling_worker)
        self.rolling_worker.block_processed_signal.connect(self._on_rolling_block_processed)
        self.rolling_worker.status_signal.connect(self._on_rolling_status)
        self.rolling_worker.finished_signal.connect(self._on_rolling_finished)
        self.rolling_worker.error_signal.connect(self._on_rolling_error)
        self.rolling_worker.start()

        self.worker.rolling_block_ready_signal.connect(self.rolling_worker.add_block)

        selected_target_app = self.combo_target_apps.currentData() if hasattr(self, "combo_target_apps") else ""

        threshold_sec = self.slider_silence.value()
        self.worker.set_auto_pause_sec(threshold_sec)
        self.worker.set_session_split_silence_sec(get_session_split_silence_sec())
        self.worker.start_recording(
            device_index=selected_mic,
            loopback_device_index=selected_loopback,
            source_mode=selected_mode,
            target_app_filter=selected_target_app,
            save_wav_path=self.current_live_wav_path,
            mic_muted=getattr(self, "_mic_is_muted", False),
            sys_muted=getattr(self, "_sys_is_muted", False)
        )
        self.timer.start()

        self.btn_start.setEnabled(False)
        self.btn_upload.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸ Wstrzymaj Ręcznie")
        self.btn_pause.setObjectName("BtnPause")
        self.btn_pause.style().unpolish(self.btn_pause)
        self.btn_pause.style().polish(self.btn_pause)
        self.btn_pause.update()

        self.btn_stop.setEnabled(True)
        self.combo_source_mode.setEnabled(False)
        self.combo_devices.setEnabled(False)
        self.combo_loopback_devices.setEnabled(False)
        if hasattr(self, "combo_target_apps"):
            mode = self.combo_source_mode.currentData() or RecordSourceMode.HYBRID_DUAL
            allow_apps = mode in (RecordSourceMode.SYSTEM_ONLY, RecordSourceMode.HYBRID_DUAL)
            self.combo_target_apps.setEnabled(allow_apps)
            self.btn_refresh_apps.setEnabled(allow_apps)
            self.combo_target_apps.setToolTip("Aplikację audio możesz w dowolnym momencie przełączyć w locie bez zatrzymywania nagrania!")
        self.btn_refresh_dev.setEnabled(False)
        self.btn_refresh_loop.setEnabled(False)
        self.combo_models.setEnabled(False)
        self.btn_auto_detect.setEnabled(False)
        self.check_enable_diarization.setEnabled(False)
        self.combo_speakers.setEnabled(False)
        self.input_token.setEnabled(False)
        self.slider_silence.setEnabled(True)
        self.slider_silence.setToolTip("Możesz w dowolnym momencie regulować próg braku mowy w trakcie nagrywania!")

    def _on_rolling_block_processed(self, block_idx, proc_sec, tot_sec, all_turns, full_plain, full_html):
        """Odebranie przetworzonego w tle bloku mowy z pełnymi word-level timestampami i synchronizacja na żywo."""
        self.current_turns = all_turns or []
        self.last_plain_text = full_plain
        if full_html:
            self.text_transcript.setHtml(full_html)
            self._scroll_transcript_view()

        # Optymalizacja: analizę mówców wykonujemy tylko wtedy, gdy włączona jest diaryzacja (Pyannote)
        if self.check_enable_diarization.isChecked():
            self._populate_speaker_mapping(self.current_turns)

        # Transmisja na żywo nowych segmentów do Supabase / CRM
        if self.cloud_sync.config.get("live_streaming") and self.cloud_sync.config.get("auto_sync") and self.current_meeting_id:
            new_segments = [t for t in (all_turns or []) if get_turn_sync_id(t) not in self._synced_turn_ids]
            if new_segments:
                for t in new_segments:
                    self._synced_turn_ids.add(get_turn_sync_id(t))
                self.synced_segment_count = len(self._synced_turn_ids)
                spk_cnt = len(set(t.get("speaker", "Mówca") for t in (all_turns or []) if t.get("speaker")))
                logger.info(
                    f"[LIVE SYNC] Blok #{block_idx}: +{len(new_segments)} nowych segmentów "
                    f"(łącznie w sesji: {self.synced_segment_count}, unikalni mówcy: {spk_cnt})"
                )
                self.cloud_sync.append_live_segments_async(
                    meeting_id=self.current_meeting_id,
                    new_segments=new_segments,
                    full_transcript=full_plain,
                    duration_seconds=tot_sec,
                    speaker_count=max(1, spk_cnt)
                )

        # Aktualizacja paska postępu
        self.last_processed_block_idx = block_idx
        pct = int(min(98, max(5, (proc_sec / max(1.0, tot_sec)) * 100)))
        p_min, p_sec = int(proc_sec // 60), int(proc_sec % 60)
        t_min, t_sec = int(tot_sec // 60), int(tot_sec % 60)
        self.progress_transcription.setValue(pct)
        self.progress_transcription.setFormat(f"🟢 Przetworzono w tle: {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d} ({pct}% · blok #{block_idx})")

    def _on_rolling_status(self, text):
        if self.worker.state in [SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]:
            self.progress_transcription.setFormat(f"Transkrypcja w tle: {text}")

    def _on_rolling_error(self, err_msg):
        if sys.stderr:
            print(f"Błąd transkrypcji w tle: {err_msg}", file=sys.stderr)

    def _on_pause_clicked(self):
        self.worker.toggle_manual_pause()

    def _on_stop_clicked(self):
        self.timer.stop()
        self.worker.stop_recording()
        self.worker.wait()

        # Odłączenie sygnału bloków
        try:
            self.worker.rolling_block_ready_signal.disconnect(self.rolling_worker.add_block)
        except Exception:
            pass

        timestamp = getattr(self, "current_live_timestamp", datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        filename = f"inteligentne_nagranie_{timestamp}.wav"
        save_path = os.path.join(self.recordings_dir, filename)

        saved = self.worker.save_wav(save_path)
        self.last_audio_save_path = save_path if saved else None

        self.progress_vu_mic.setValue(0)
        self.progress_vu_sys.setValue(0)
        self.progress_silence.setValue(0)
        self.lbl_silence_val.setText(f"0.0 s / {self.slider_silence.value()}.0 s")
        self.lbl_vad_detail.setText("VAD: Oczekiwanie na uruchomienie...")

        # Blokada przycisku Start do czasu zakończenia finalizacji
        self._finalize_pending = True
        self.btn_start.setEnabled(False)
        self.btn_upload.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸ Wstrzymaj Ręcznie")
        self.btn_pause.setObjectName("BtnPause")
        self.btn_pause.style().unpolish(self.btn_pause)
        self.btn_pause.style().polish(self.btn_pause)
        self.btn_pause.update()

        self.btn_stop.setEnabled(False)
        self.combo_source_mode.setEnabled(True)
        self._on_source_mode_changed()
        self.combo_models.setEnabled(True)
        self.btn_auto_detect.setEnabled(True)
        self.check_enable_diarization.setEnabled(True)
        is_diar = self.check_enable_diarization.isChecked()
        self.combo_speakers.setEnabled(is_diar)
        self.input_token.setEnabled(is_diar)
        self.slider_silence.setEnabled(True)

        if getattr(self, "_mic_is_muted", False):
            self._toggle_mic_mute()
        if getattr(self, "_sys_is_muted", False):
            self._toggle_sys_mute()

        if saved:
            self._refresh_recordings_list()
            self._refresh_transcriptions_list()

            # Pobranie wszystkich zaległych bloków audio (mikrofon + loopback)
            final_block = None
            if hasattr(self.worker, "get_remaining_blocks"):
                rem_blocks = self.worker.get_remaining_blocks()
                if rem_blocks:
                    for b_idx, b_st, b_en, b_arr, b_ch in rem_blocks[:-1]:
                        if getattr(self, "rolling_worker", None) is not None:
                            self.rolling_worker.add_block(b_idx, b_st, b_en, b_arr, channel_source=b_ch)
                    last_idx, last_st, last_en, last_arr, last_ch = rem_blocks[-1]
                    final_block = RollingBlock(last_idx, last_st, last_en, last_arr, channel_source=last_ch)
            elif hasattr(self.worker, "get_remaining_block"):
                remaining = self.worker.get_remaining_block()
                if remaining:
                    r_idx, r_start, r_end, r_audio = remaining
                    final_block = RollingBlock(r_idx, r_start, r_end, r_audio)

            self.progress_transcription.setFormat("Finalizowanie ostatniego fragmentu rozmowy w tle...")
            self.progress_transcription.setValue(95)

            # Przekazanie ostatniego fragmentu do finalizacji
            if getattr(self, "rolling_worker", None) is not None:
                self.rolling_worker.stop_and_finalize(final_block)
        else:
            self._finalize_pending = False
            self.btn_start.setEnabled(True)
            QMessageBox.warning(self, "Brak Nagrania", "Nie zarejestrowano mowy do zapisu.")


    def _on_rolling_finished(self, final_html: str, final_plain: str, all_turns: list):
        """Zakończenie przetwarzania w tle po kliknięciu Stop."""
        words = []
        if hasattr(self, "rolling_worker") and self.rolling_worker:
            if hasattr(self.rolling_worker, "get_all_words"):
                words = self.rolling_worker.get_all_words()
            if self.rolling_worker in self._active_threads:
                self._active_threads.remove(self.rolling_worker)

        # Fallback pobrania słów z sesji JSON jeśli rolling_worker był już wyczyszczony
        if not words and hasattr(self, "current_live_txt_path") and self.current_live_txt_path:
            try:
                j_path = get_session_path_for_txt(self.current_live_txt_path)
                if os.path.exists(j_path):
                    s = TranscriptionSession.load_from_json(j_path)
                    if s and s.words:
                        words = s.words
            except Exception:
                pass

        self.current_turns = all_turns or []
        self.last_plain_text = final_plain
        self.text_transcript.setHtml(final_html)
        self._scroll_transcript_view()
        self._populate_speaker_mapping(self.current_turns)

        enable_diar = self.check_enable_diarization.isChecked()
        token = self.input_token.text().strip()
        spk_cfg = self.combo_speakers.currentData() or {}
        num_spk = spk_cfg.get("num_speakers")
        min_spk = spk_cfg.get("min_speakers")
        max_spk = spk_cfg.get("max_speakers")

        # Jeśli użytkownik zażądał diaryzacji mówców PyAnnote (reużycie gotowych słów z Whispera bez ponownego uruchamiania)
        if enable_diar and token and self.last_audio_save_path and words:
            self.progress_transcription.setFormat("Trwa analiza głosów i podział na mówców (PyAnnote)...")
            self.progress_transcription.setValue(5)

            json_path = None
            if hasattr(self, "current_live_txt_path") and self.current_live_txt_path:
                json_path = get_session_path_for_txt(self.current_live_txt_path)

            self.diarization_thread = DiarizationOnlyWorker(
                audio_path=self.last_audio_save_path,
                transcript_words=words,
                hf_token=token,
                session_json_path=json_path,
                num_speakers=num_spk,
                min_speakers=min_spk,
                max_speakers=max_spk
            )
            self._active_threads.append(self.diarization_thread)
            self.diarization_thread.progress_signal.connect(self._on_transcription_progress)
            self.diarization_thread.finished_signal.connect(self._on_rolling_diarization_finished)
            self.diarization_thread.error_signal.connect(self._on_transcription_error)
            self.diarization_thread.start()
        else:
            self._on_transcription_finished(final_html, final_plain, self.current_turns)

    def _on_rolling_diarization_finished(self, html_text: str, plain_text: str, turns: list, session_path: str):
        """Obsługa zakończenia samej diaryzacji na słowach z rolling-transkrypcji."""
        if hasattr(self, "diarization_thread") and self.diarization_thread in self._active_threads:
            self._active_threads.remove(self.diarization_thread)
        self._on_transcription_finished(html_text, plain_text, turns)


    def _on_upload_file_clicked(self):
        """
        Obsługa wgrywania zewnętrznego pliku audio/wideo (WAV, MP3, M4A, FLAC, OGG, AAC, MP4, MKV)
        bez konieczności posiadania zewnętrznego narzędzia FFmpeg w systemie Windows.
        """
        file_filter = (
            "Wszystkie Obsługiwane (*.mp4 *.mkv *.mov *.webm *.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma);;"
            "Nagrania Wideo (*.mp4 *.mkv *.mov *.webm);;"
            "Nagrania Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma);;"
            "Wszystkie pliki (*.*)"
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik audio do transkrypcji",
            "",
            file_filter
        )

        if not file_path:
            return

        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            QMessageBox.warning(self, "Niepoprawny Plik", "Wybrany plik jest pusty lub nie istnieje na dysku.")
            return

        token = self.input_token.text().strip()
        filename = os.path.basename(file_path)
        selected_model = self.combo_models.currentData() or DEFAULT_WHISPER_MODEL
        enable_diar = self.check_enable_diarization.isChecked()
        spk_cfg = self.combo_speakers.currentData() or {}
        num_spk = spk_cfg.get("num_speakers")
        min_spk = spk_cfg.get("min_speakers")
        max_spk = spk_cfg.get("max_speakers")

        # Blokowanie kontrolek na czas przetwarzania pliku
        self.btn_start.setEnabled(False)
        self.btn_upload.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.combo_devices.setEnabled(False)
        self.combo_models.setEnabled(False)
        self.btn_auto_detect.setEnabled(False)
        self.check_enable_diarization.setEnabled(False)
        self.combo_speakers.setEnabled(False)
        self.input_token.setEnabled(False)

        self.text_transcript.clear()
        self.text_transcript.setPlaceholderText(f"Trwa przetwarzanie pliku '{filename}'...\nProszę czekać, operacja odbywa się asynchronicznie.")
        self.progress_transcription.setValue(0)
        self.progress_transcription.setFormat(f"Inicjalizacja przetwarzania: {filename}")

        self.file_processing_worker = FileProcessingWorker(
            input_file_path=file_path,
            recordings_dir=self.recordings_dir,
            hf_token=token if token else None,
            model_size=selected_model,
            enable_diarization=enable_diar,
            num_speakers=num_spk,
            min_speakers=min_spk,
            max_speakers=max_spk
        )
        self._active_threads.append(self.file_processing_worker)
        self.file_processing_worker.progress_signal.connect(self._on_file_progress)
        self.file_processing_worker.preliminary_signal.connect(self._on_file_preliminary_transcript)
        self.file_processing_worker.finished_signal.connect(self._on_file_finished)
        self.file_processing_worker.error_signal.connect(self._on_file_error)
        self.file_processing_worker.start()

    def _on_preliminary_transcript(self, html_text: str, plain_text: str, turns: list = None):
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()
        self.current_turns = turns or []
        self._populate_speaker_mapping(self.current_turns)

    def _on_file_preliminary_transcript(self, html_text: str, plain_text: str, prepared_wav_path: str, turns: list = None):
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()
        base_name = os.path.basename(prepared_wav_path)
        file_stem = os.path.splitext(base_name)[0]
        txt_filename = f"transkrypcja_{file_stem}.txt"
        self.current_txt_path = os.path.join(self.transcriptions_dir, txt_filename)
        self.current_turns = turns or []
        self._refresh_transcriptions_list()
        self._populate_speaker_mapping(self.current_turns)

    def _on_file_progress(self, value: int, text: str):
        self.progress_transcription.setValue(value)
        self.progress_transcription.setFormat(f"{value}% - {text}")

    def _on_file_finished(self, html_text: str, plain_text: str, prepared_wav_path: str, turns: list = None):
        if hasattr(self, "file_processing_worker") and self.file_processing_worker in self._active_threads:
            self._active_threads.remove(self.file_processing_worker)

        self.progress_transcription.setValue(100)
        self.progress_transcription.setFormat("Przetwarzanie pliku zakończone!")
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()

        # Odblokowanie kontrolek
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self.combo_devices.setEnabled(True)
        self.combo_models.setEnabled(True)
        self.btn_auto_detect.setEnabled(True)
        self.check_enable_diarization.setEnabled(True)
        is_diar = self.check_enable_diarization.isChecked()
        self.combo_speakers.setEnabled(is_diar)
        self.input_token.setEnabled(is_diar)

        self.last_audio_save_path = prepared_wav_path
        self._refresh_recordings_list()

        # Zapis transkrypcji do pliku TXT
        base_name = os.path.basename(prepared_wav_path)
        file_stem = os.path.splitext(base_name)[0]
        txt_filename = f"transkrypcja_{file_stem}.txt"
        txt_path = os.path.join(self.transcriptions_dir, txt_filename)
        self.current_txt_path = txt_path
        self.current_turns = turns or []

        # Wypełnienie panelu mapowania mówców
        self._populate_speaker_mapping(self.current_turns)

        # Jeśli wykryto pewne sugestie imion, automatycznie aktualizujemy treść
        suggestions = suggest_speaker_names(self.current_turns) if self.current_turns else {}
        if suggestions and any(k != v for k, v in suggestions.items()):
            auto_html, auto_plain = format_turns(self.current_turns, suggestions)
            self.text_transcript.setHtml(auto_html)
            self._scroll_transcript_view()
            plain_text = auto_plain

        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(plain_text)

            # Zapis / Aktualizacja pliku sesji JSON
            json_path = get_session_path_for_txt(txt_path)
            try:
                has_diar = any(t.get("speaker", "").startswith("SPEAKER_") for t in (turns or []))
                session = TranscriptionSession.load_from_json(json_path) or TranscriptionSession()
                session.has_transcription = True
                session.has_diarization = has_diar
                session.prepared_wav = prepared_wav_path
                session.source_audio = prepared_wav_path
                session.turns = self.current_turns
                session.save_to_json(json_path)
            except Exception:
                pass

            self._refresh_transcriptions_list()
        except Exception as e:
            if sys.stderr:
                print(f"Błąd zapisu pliku TXT: {e}", file=sys.stderr)

        self.last_plain_text = plain_text
        self.btn_manual_sync.setEnabled(True)

        # Automatyczna synchronizacja z chmurą / EMANAGER.PRO
        if self.cloud_sync.config.get("auto_sync"):
            self._trigger_cloud_sync(
                plain_text=plain_text,
                turns=self.current_turns,
                audio_path=prepared_wav_path,
                title=f"Plik: {base_name}",
                silent=True
            )

        QMessageBox.information(
            self,
            "Plik Przetworzony",
            f"Pomyślnie przetworzono plik audio!\n\n"
            f"Zapisano audio 16kHz:\n{os.path.basename(prepared_wav_path)}\n\n"
            f"Zapisano transkrypcję:\n{txt_filename}"
        )

    def _on_file_error(self, err_msg: str):
        self.progress_transcription.setValue(0)
        self.progress_transcription.setFormat("Błąd przetwarzania pliku!")
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self.combo_devices.setEnabled(True)
        self.combo_models.setEnabled(True)
        self.btn_auto_detect.setEnabled(True)
        self.check_enable_diarization.setEnabled(True)
        is_diar = self.check_enable_diarization.isChecked()
        self.combo_speakers.setEnabled(is_diar)
        self.input_token.setEnabled(is_diar)
        QMessageBox.critical(self, "Błąd Przetwarzania Pliku", f"Wystąpił błąd podczas przetwarzania pliku audio:\n\n{err_msg}")

    def _on_transcription_progress(self, value, text):
        self.progress_transcription.setValue(value)
        self.progress_transcription.setFormat(f"{value}% - {text}")

    def _on_transcription_finished(self, html_text: str, plain_text: str, turns: list = None):
        if hasattr(self, "transcription_thread") and self.transcription_thread in self._active_threads:
            self._active_threads.remove(self.transcription_thread)

        self.progress_transcription.setValue(100)
        self.progress_transcription.setFormat("Transkrypcja zakończona!")
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self.combo_devices.setEnabled(True)
        self.combo_models.setEnabled(True)
        self.btn_auto_detect.setEnabled(True)
        self.check_enable_diarization.setEnabled(True)
        is_diar = self.check_enable_diarization.isChecked()
        self.combo_speakers.setEnabled(is_diar)
        self.input_token.setEnabled(is_diar)

        if self.last_audio_save_path:
            base_name = os.path.basename(self.last_audio_save_path)
            file_stem = os.path.splitext(base_name)[0]
            txt_filename = f"transkrypcja_{file_stem.replace('inteligentne_nagranie_', '')}.txt"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            txt_filename = f"transkrypcja_{timestamp}.txt"

        txt_path = os.path.join(self.transcriptions_dir, txt_filename)
        self.current_txt_path = txt_path
        self.current_turns = turns or []

        # Sprawdzenie czy w wynikach są klastry diaryzacji (SPEAKER_XX)
        has_diarization = any(t.get("speaker", "").startswith("SPEAKER_") for t in self.current_turns)

        if has_diarization:
            # Wypełnienie panelu mapowania mówców
            self._populate_speaker_mapping(self.current_turns)

            suggestions = suggest_speaker_names(self.current_turns) if self.current_turns else {}
            if suggestions and any(k != v for k, v in suggestions.items()):
                auto_html, auto_plain = format_turns(self.current_turns, suggestions)
                self.text_transcript.setHtml(auto_html)
                self._scroll_transcript_view()
                plain_text = auto_plain
        else:
            # Gdy diaryzacja jest wyłączona: panel mapowania jest ukryty, a w transkrypcji pozostaje neutralny 'Mówca'
            self.speaker_box.setVisible(False)

        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(plain_text)

            # Zapis / Aktualizacja pliku sesji JSON
            json_path = get_session_path_for_txt(txt_path)
            try:
                session = TranscriptionSession.load_from_json(json_path) or TranscriptionSession()
                session.has_transcription = True
                session.has_diarization = has_diarization
                if self.last_audio_save_path:
                    session.prepared_wav = self.last_audio_save_path
                    session.source_audio = self.last_audio_save_path
                session.turns = self.current_turns
                session.save_to_json(json_path)
            except Exception:
                pass

            self._refresh_transcriptions_list()
        except Exception as e:
            if sys.stderr:
                print(f"Błąd zapisu pliku TXT: {e}", file=sys.stderr)

        self.last_plain_text = plain_text
        self.btn_manual_sync.setEnabled(True)

        # Zakończenie finalizacji — odblokowanie przycisku Start
        self._finalize_pending = False
        self.btn_start.setEnabled(True)

        # Automatyczna synchronizacja z chmurą / EMANAGER.PRO
        if self.cloud_sync.config.get("auto_sync"):
            self._trigger_cloud_sync(
                plain_text=plain_text,
                turns=self.current_turns,
                audio_path=self.last_audio_save_path,
                title=f"Nagranie: {txt_filename}",
                silent=True
            )

    def _populate_speaker_mapping(self, turns: list):
        """
        Dynamicznie buduje listę wykrytych mówców w panelu weryfikacji.
        """
        while self.speaker_rows_layout.count():
            child = self.speaker_rows_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                while child.layout().count():
                    subchild = child.layout().takeAt(0)
                    if subchild.widget():
                        subchild.widget().deleteLater()

        self.speaker_inputs.clear()

        if not turns:
            self.speaker_box.setVisible(False)
            return

        speakers = sorted(list(set(t.get("speaker", "") for t in turns if t.get("speaker"))))
        has_diarization = any(spk.startswith("SPEAKER_") for spk in speakers)

        if not has_diarization or len(speakers) == 0:
            self.speaker_box.setVisible(False)
            return

        # Analiza dowodów i autosugestie
        suggestions = suggest_speaker_names(turns)
        evidence = analyze_speakers(turns)

        for spk_id in speakers:
            suggested_name = suggestions.get(spk_id, spk_id)
            ev = evidence.get(spk_id, {})
            clue = ev.get("clue", "Brak jednoznacznego dowodu w tekście")
            sample_text = ev.get("sample", "")
            spk_count = ev.get("count", 0)
            spk_dur = ev.get("total_duration", 0.0)
            stats_text = format_speaker_stats(spk_count, spk_dur)

            card_frame = QFrame()
            card_frame.setStyleSheet("background-color: #1a1a2e; border: 1px solid #3d3d5c; border-radius: 8px; padding: 6px;")
            card_layout = QVBoxLayout(card_frame)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(6)

            # Nagłówek karty: ID Mówcy + Licznik wypowiedzi i łączny czas mowy
            header_row = QHBoxLayout()
            lbl_spk = QLabel(f"🏷️ <b>{spk_id}</b>")
            lbl_spk.setStyleSheet("color: #edf2f4; font-size: 12px; font-weight: bold;")

            lbl_stats = QLabel(f"📊 {stats_text}")
            lbl_stats.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")
            lbl_stats.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            header_row.addWidget(lbl_spk)
            header_row.addStretch(1)
            header_row.addWidget(lbl_stats)
            card_layout.addLayout(header_row)

            # Wiersz inputów: Pole Imienia + Pole Roli / Firmy
            input_row = QHBoxLayout()
            input_row.setSpacing(8)

            edit_name = QLineEdit()
            edit_name.setPlaceholderText("Imię / Nazwisko (np. Ania, Bartek)...")
            edit_name.setText(suggested_name if suggested_name != spk_id else "")
            edit_name.setStyleSheet("background-color: #2b2d42; color: #edf2f4; border: 1px solid #4cc9f0; border-radius: 4px; padding: 5px 8px; font-weight: bold; font-size: 12px;")

            edit_role = QLineEdit()
            edit_role.setPlaceholderText("Rola / Dział (np. Kierownik, Sprzedaż, IT)...")
            edit_role.setStyleSheet("background-color: #2b2d42; color: #f59e0b; border: 1px solid #f59e0b; border-radius: 4px; padding: 5px 8px; font-size: 11px;")

            self.speaker_inputs[spk_id] = {
                "name": edit_name,
                "role": edit_role
            }

            input_row.addWidget(edit_name, stretch=3)
            input_row.addWidget(edit_role, stretch=2)
            card_layout.addLayout(input_row)

            # Dolny wiersz: Wskazówka kontekstowa z dowodem oraz próbka wypowiedzi
            bottom_row = QHBoxLayout()
            bottom_row.setSpacing(10)

            lbl_clue = QLabel(f"💡 {clue}")
            lbl_clue.setStyleSheet("color: #4cc9f0; font-size: 11px;")
            lbl_clue.setWordWrap(True)

            lbl_sample = QLabel(f"Próbka: <i>„{sample_text}”</i>" if sample_text else "")
            lbl_sample.setStyleSheet("color: #8d99ae; font-size: 11px;")
            lbl_sample.setWordWrap(True)

            bottom_row.addWidget(lbl_clue, stretch=2)
            bottom_row.addWidget(lbl_sample, stretch=3)
            card_layout.addLayout(bottom_row)

            self.speaker_rows_layout.addWidget(card_frame)

        self.speaker_box.setVisible(True)

    def _on_apply_speakers_clicked(self):
        """
        Zatwierdza nowe nazwy i role mówców wprowadzone przez użytkownika i aktualizuje podgląd oraz plik TXT.
        """
        if not self.current_turns:
            return

        mapping = {}
        for spk_id, fields in self.speaker_inputs.items():
            if isinstance(fields, dict):
                name_val = fields["name"].text().strip()
                role_val = fields["role"].text().strip()
                
                # Scalanie: np. "Łukasz (emanager)" lub samo "Łukasz"
                if name_val and role_val:
                    label = f"{name_val} ({role_val})"
                elif name_val:
                    label = name_val
                elif role_val:
                    label = f"{spk_id} ({role_val})"
                else:
                    label = spk_id
            else:
                val = fields.text().strip()
                label = val if val else spk_id

            mapping[spk_id] = label

        # Zaktualizuj etykiety mówców w turns
        for t in self.current_turns:
            orig_spk = t.get("speaker")
            if orig_spk in mapping:
                t["speaker"] = mapping[orig_spk]

        # Ustalenie bazowej daty/godziny sesji do zachowania formatu timestampu
        session_dt = getattr(self, "current_session_start_time", None)
        if not session_dt and self.current_txt_path:
            session_dt = extract_datetime_from_filename(self.current_txt_path)
        if not session_dt and self.last_audio_save_path:
            session_dt = extract_datetime_from_filename(self.last_audio_save_path)

        if self.current_txt_path:
            json_path = get_session_path_for_txt(self.current_txt_path)
            if os.path.exists(json_path):
                sess = TranscriptionSession.load_from_json(json_path)
                if sess:
                    sess.update_speaker_mapping(mapping)
                    sess.turns = self.current_turns
                    sess.save_to_json(json_path)
                    if sess.created_at and not session_dt:
                        try:
                            session_dt = datetime.fromisoformat(sess.created_at)
                        except Exception:
                            pass

        html_text, plain_text = format_turns(self.current_turns, mapping, session_start_time=session_dt)
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()
        self.last_plain_text = plain_text

        if self.current_txt_path:
            try:
                with open(self.current_txt_path, 'w', encoding='utf-8') as f:
                    f.write(plain_text)
                self._refresh_transcriptions_list()

                # Ponowna synchronizacja z chmurą ze zweryfikowanymi imionami
                self._trigger_cloud_sync(
                    plain_text=plain_text,
                    turns=self.current_turns,
                    audio_path=self.last_audio_save_path,
                    title=f"Zweryfikowano: {os.path.basename(self.current_txt_path or 'Spotkanie')}",
                    silent=False
                )

                QMessageBox.information(
                    self,
                    "Zaktualizowano Mówców",
                    "Pomyślnie zaktualizowano imiona mówców w podglądzie, pliku TXT oraz przesłano aktualizację do chmury!"
                )
            except Exception as e:
                QMessageBox.warning(self, "Błąd Zapisu", f"Nie udało się zaktualizować pliku TXT:\n{e}")

    def _trigger_cloud_sync(self, plain_text: str, turns: list, audio_path: Optional[str] = None, title: Optional[str] = None, silent: bool = False):
        """Wysyła sesję do menedżera synchronizacji CloudSyncManager."""
        if not plain_text or not plain_text.strip():
            if not silent:
                QMessageBox.warning(self, "Brak Treści", "Brak tekstu transkrypcji do wysłania.")
            return

        # Rzeczywista długość nagrania: priorytet 1 to plik audio, priorytet 2 stoper, priorytet 3 ostatni segment
        duration = 0.0
        if audio_path and os.path.exists(audio_path):
            try:
                import soundfile as sf
                info = sf.info(audio_path)
                duration = float(info.duration)
            except Exception:
                pass

        if duration <= 0.0 and self.recorded_seconds > 0:
            duration = float(self.recorded_seconds)

        if duration <= 0.0 and turns:
            try:
                duration = max(float(t.get("end", 0.0)) for t in turns)
            except Exception:
                pass

        # Przekształć turns na segmenty dla API
        segments = []
        for t in (turns or []):
            segments.append({
                "speaker": t.get("speaker", "Mówca"),
                "start": t.get("start", 0.0),
                "end": t.get("end", 0.0),
                "text": t.get("text", "")
            })

        self.current_meeting_id = self.cloud_sync.sync_meeting_async(
            title=title or f"Spotkanie {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            transcript_text=plain_text,
            segments=segments,
            duration_seconds=duration,
            audio_path=audio_path,
            context_type="general",
            meeting_id=self.current_meeting_id
        )

        # Zapisz meeting_id do pliku sesji JSON, aby późniejsze operacje (np. modułowa diaryzacja) aktualizowały dokładnie ten sam rekord
        if getattr(self, "current_txt_path", None):
            try:
                json_path = get_session_path_for_txt(self.current_txt_path)
                sess = TranscriptionSession.load_from_json(json_path) or TranscriptionSession()
                sess.meeting_id = self.current_meeting_id
                sess.save_to_json(json_path)
            except Exception:
                pass

    def _on_manual_sync_clicked(self):
        """Ręczne wywołanie wysyłki z przycisku w UI."""
        if not self.last_plain_text:
            QMessageBox.warning(self, "Brak Transkrypcji", "Wykonaj nagranie lub wczytaj transkrypcję przed wysłaniem.")
            return

        self._trigger_cloud_sync(
            plain_text=self.last_plain_text,
            turns=self.current_turns,
            audio_path=self.last_audio_save_path,
            title=f"Ręczny Eksport: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            silent=False
        )

    def _on_sync_started(self, meeting_id: str):
        target_name = self.cloud_sync.config.get("sync_target", "emanager").upper()
        self.lbl_cloud_status.setText(f"☁️ Synchronizacja z {target_name} w toku...")
        self.lbl_cloud_status.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: bold;")
        self.btn_manual_sync.setEnabled(False)

    def _on_sync_finished(self, meeting_id: str, success: bool, message: str):
        target_name = self.cloud_sync.config.get("sync_target", "emanager").upper()
        self.btn_manual_sync.setEnabled(True)
        if success:
            self.lbl_cloud_status.setText(f"☁️ Zsynchronizowano z {target_name} ✅")
            self.lbl_cloud_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")
        else:
            self.lbl_cloud_status.setText(f"☁️ Zapisano lokalnie (kolejka offline)")
            self.lbl_cloud_status.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: bold;")

    def _on_offline_queued(self, meeting_id: str, message: str):
        self.lbl_cloud_status.setText(f"☁️ Zapisano w kolejce offline ⏳")
        self.lbl_cloud_status.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: bold;")
        self.btn_manual_sync.setEnabled(True)

    def _on_live_session_started(self, meeting_id: str):
        target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
        self.lbl_cloud_status.setText(f"🟢 Transmisja na żywo do {target_name} aktywna (ID: {meeting_id[:8]}...)")
        self.lbl_cloud_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")

    def _on_live_block_synced(self, meeting_id: str, count: int):
        target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
        self.lbl_cloud_status.setText(f"🟢 Transmisja do {target_name}: +{count} wypowiedzi na żywo")
        self.lbl_cloud_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")

    def _on_live_session_finalized(self, meeting_id: str, success: bool, msg: str):
        target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
        if success:
            self.lbl_cloud_status.setText(f"☁️ Zakończono sesję w {target_name} ✅")
            self.lbl_cloud_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")
        else:
            self.lbl_cloud_status.setText(f"☁️ Sesja zapisana lokalnie (kolejka offline)")
            self.lbl_cloud_status.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: bold;")

    def _on_session_split_triggered(self, reason: str):
        """
        Automatycznie zamyka bieżące spotkanie (upload audio do Storage, status='completed')
        i rozpoczyna nowe spotkanie w Supabase bez przerywania ciągłego nasłuchu mikrofonu.
        """
        print(f"[SMART SESSION] Podział sesji wywołany przez: {reason}")
        
        # 1. Zachowaj metadane zamykanej sesji
        old_meeting_id = self.current_meeting_id
        old_plain_text = self.last_plain_text
        old_recorded_sec = float(self.recorded_seconds)
        old_wav_path = getattr(self, "current_live_wav_path", None)
        old_turns = self.current_turns
        old_timestamp = getattr(self, 'current_live_timestamp', '')

        # 2. Generowanie nowych ścieżek dla kolejnego spotkania
        split_now = datetime.now()
        new_timestamp = split_now.strftime("%Y%m%d_%H%M%S_%f")
        self.session_start_time = split_now
        self.current_live_timestamp = new_timestamp
        self.current_live_wav_path = os.path.join(self.recordings_dir, f"inteligentne_nagranie_{new_timestamp}.wav")
        self.current_live_txt_path = os.path.join(self.transcriptions_dir, f"transkrypcja_{new_timestamp}.txt")
        try:
            with open(self.current_live_txt_path, 'w', encoding='utf-8') as f:
                f.write(f"=== NOWE SPOTKANIE BIUROWE (Start: {split_now.strftime('%Y-%m-%d %H:%M:%S')}) ===\n\n")
            self._refresh_transcriptions_list()
        except Exception:
            pass

        # 3. Rotacja rejestratora audio (flushez i zamyka stary WAV na dysku!) i transkrypcji w tle
        self.worker.rotate_session_file(self.current_live_wav_path)
        self._refresh_recordings_list()
        if hasattr(self, "rolling_worker") and self.rolling_worker is not None:
            self.rolling_worker.reset_for_new_session(self.current_live_txt_path, session_start_time=split_now)

        # 4. Finalizacja poprzedniej sesji w Supabase (gdy stary WAV jest już w 100% zamknięty na dysku)
        if old_meeting_id and old_plain_text:
            self.cloud_sync.finalize_live_session_async(
                meeting_id=old_meeting_id,
                final_transcript=old_plain_text,
                duration_seconds=old_recorded_sec,
                audio_path=old_wav_path,
                turns=old_turns,
                title=f"Spotkanie biurowe {old_timestamp}"
            )

        self.synced_segment_count = 0
        self._synced_turn_ids = set()
        self.current_turns = []
        self.last_plain_text = ""
        self.recorded_seconds = 0
        self._active_recorded_time = 0.0
        self._last_active_tick = None
        self.lbl_timer.setText("00:00:00")
        self.text_transcript.setHtml("<div style='color: #94a3b8; font-style: italic; text-align: center; padding: 20px;'>✨ Rozpoczęto nowe spotkanie biurowe (poprzednia sesja została automatycznie zapisana)...</div>")

        # 5. Start nowej sesji w Supabase
        if self.cloud_sync.config.get("live_streaming") and self.cloud_sync.config.get("auto_sync"):
            self.current_meeting_id = self.cloud_sync.start_live_session_async(
                title=f"Spotkanie biurowe {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

        target_name = self.cloud_sync.config.get("sync_target", "CRM").upper()
        self.lbl_cloud_status.setText(f"🟢 Nowa sesja spotkania w {target_name} ({reason})")
        self.lbl_cloud_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")

    def _refresh_transcriptions_list(self):
        """Odświeża listę transkrypcji TXT posortowaną chronologicznie (najnowsze na samej górze)."""
        self.list_transcriptions.clear()
        if not os.path.exists(self.transcriptions_dir):
            return

        full_paths = [os.path.join(self.transcriptions_dir, f) for f in os.listdir(self.transcriptions_dir) if f.endswith(".txt")]
        full_paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        for full_path in full_paths:
            filename = os.path.basename(full_path)
            size_kb = os.path.getsize(full_path) / 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%Y-%m-%d %H:%M:%S")

            json_path = get_session_path_for_txt(full_path)
            badge = ""
            if os.path.exists(json_path):
                sess = TranscriptionSession.load_from_json(json_path)
                if sess:
                    badge = f"  {sess.get_status_badge()}"

            item = QListWidgetItem(f"📄 {filename}{badge}  ({size_kb:.1f} KB, {mtime})")
            item.setData(Qt.ItemDataRole.UserRole, full_path)
            self.list_transcriptions.addItem(item)

    def _on_run_diarization_clicked(self):
        """Uruchamia modułową analizę mówców (PyAnnote) dla zaznaczonego nagrania bez ponownego uruchamiania Whispera."""
        target_txt = None
        current_item = self.list_transcriptions.currentItem()
        if current_item:
            target_txt = current_item.data(Qt.ItemDataRole.UserRole)
        elif self.current_txt_path and os.path.exists(self.current_txt_path):
            target_txt = self.current_txt_path

        if not target_txt or not os.path.exists(target_txt):
            QMessageBox.warning(self, "Wybierz Transkrypcję", "Wybierz z listy po prawej stronie transkrypcję, dla której chcesz wykonać podział na mówców.")
            return

        token = self.input_token.text().strip()
        if not token:
            QMessageBox.warning(
                self,
                "Wymagany Token HuggingFace",
                "Do uruchomienia modułu diaryzacji PyAnnote wymagany jest bezpłatny token HuggingFace.\n\n"
                "Wklej swój token w polu 'HuggingFace Token' i spróbuj ponownie."
            )
            self.input_token.setFocus()
            return

        # Poszukiwanie odpowiadającego pliku audio WAV i sesji JSON
        json_path = get_session_path_for_txt(target_txt)
        session = TranscriptionSession.load_from_json(json_path) if os.path.exists(json_path) else None

        wav_path = None
        if session and session.prepared_wav and os.path.exists(session.prepared_wav):
            wav_path = session.prepared_wav
        elif session and session.source_audio and os.path.exists(session.source_audio):
            wav_path = session.source_audio
        else:
            # Dopasowanie po nazwie pliku w katalogu recordings/
            txt_basename = os.path.basename(target_txt)
            clean_stem = txt_basename.replace("transkrypcja_", "").replace(".txt", "")
            for rec_name in os.listdir(self.recordings_dir):
                if clean_stem in rec_name and rec_name.endswith(".wav"):
                    wav_path = os.path.join(self.recordings_dir, rec_name)
                    break

        if not wav_path or not os.path.exists(wav_path):
            # Użytkownik może wskazać plik audio ręcznie
            QMessageBox.information(
                self,
                "Wskaż Plik Audio",
                f"Nie odnaleziono automatycznie pliku nagrania dla:\n{os.path.basename(target_txt)}\n\n"
                "Wskaż plik audio (.wav, .mp3, .m4a) odpowiadający tej transkrypcji."
            )
            wav_path, _ = QFileDialog.getOpenFileName(
                self,
                "Wybierz plik audio do diaryzacji",
                self.recordings_dir,
                "Pliki Audio (*.wav *.mp3 *.m4a *.flac *.ogg);;Wszystkie (*.*)"
            )
            if not wav_path:
                return

        # Pobranie słów z sesji JSON lub estymacja z pliku TXT
        words = []
        if session and session.words:
            words = session.words
        else:
            try:
                with open(target_txt, 'r', encoding='utf-8') as f:
                    txt_content = f.read()
                parsed_turns = parse_txt_to_turns(txt_content)
                for t in parsed_turns:
                    t_words = t.get("text", "").split()
                    st = t.get("start", 0.0)
                    en = t.get("end", st + 1.0)
                    dur = (en - st) / max(1, len(t_words))
                    for i, w_str in enumerate(t_words):
                        words.append({
                            "word": (" " + w_str if i > 0 else w_str),
                            "start": round(st + (i * dur), 2),
                            "end": round(st + ((i + 1) * dur), 2),
                            "probability": 0.95
                        })
            except Exception:
                pass

        if not words:
            QMessageBox.warning(self, "Brak Treści", "Nie udało się odczytać słów z wybranej transkrypcji.")
            return

        spk_cfg = self.combo_speakers.currentData() or {}
        num_spk = spk_cfg.get("num_speakers")
        min_spk = spk_cfg.get("min_speakers")
        max_spk = spk_cfg.get("max_speakers")

        # Blokowanie kontrolek
        self.btn_start.setEnabled(False)
        self.btn_upload.setEnabled(False)
        self.btn_run_diarization.setEnabled(False)
        self.progress_transcription.setValue(5)
        self.progress_transcription.setFormat("Uruchamianie analizy osób PyAnnote (w tle)...")

        self.current_txt_path = target_txt
        self.last_audio_save_path = wav_path
        if session and getattr(session, "meeting_id", None):
            self.current_meeting_id = session.meeting_id
        elif wav_path:
            stem = os.path.splitext(os.path.basename(wav_path))[0].replace("inteligentne_nagranie_", "")
            self.current_meeting_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"recorder67_{stem}"))

        self.diarization_thread = DiarizationOnlyWorker(
            audio_path=wav_path,
            transcript_words=words,
            hf_token=token,
            session_json_path=json_path,
            num_speakers=num_spk,
            min_speakers=min_spk,
            max_speakers=max_spk
        )
        self._active_threads.append(self.diarization_thread)
        self.diarization_thread.progress_signal.connect(self._on_file_progress)
        self.diarization_thread.finished_signal.connect(self._on_diarization_only_finished)
        self.diarization_thread.error_signal.connect(self._on_transcription_error)
        self.diarization_thread.start()

    def _on_diarization_only_finished(self, html_text: str, plain_text: str, turns: list, session_path: str):
        if hasattr(self, "diarization_thread") and self.diarization_thread in self._active_threads:
            self._active_threads.remove(self.diarization_thread)

        self.progress_transcription.setValue(100)
        self.progress_transcription.setFormat("Diaryzacja mówców zakończona pomyślnie!")
        self.text_transcript.setHtml(html_text)
        self._scroll_transcript_view()
        self.current_turns = turns or []
        self.last_plain_text = plain_text

        # Aktualizacja pliku TXT
        if self.current_txt_path:
            try:
                with open(self.current_txt_path, 'w', encoding='utf-8') as f:
                    f.write(plain_text)
            except Exception:
                pass

        # Odświeżenie UI i panelu weryfikacji
        self._refresh_transcriptions_list()
        self._populate_speaker_mapping(self.current_turns)

        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self.btn_run_diarization.setEnabled(True)
        self.btn_manual_sync.setEnabled(True)

        # Bezpieczna synchronizacja z chmurą / EMANAGER.PRO (aktualizacja rekordów mówców metodą PATCH)
        if self.cloud_sync.config.get("auto_sync"):
            self._trigger_cloud_sync(
                plain_text=plain_text,
                turns=self.current_turns,
                audio_path=self.last_audio_save_path,
                title=None,
                silent=True
            )

        QMessageBox.information(
            self,
            "Diaryzacja Zakończona",
            f"Pomyślnie wykonano podział na mówców!\n\n"
            f"Wykryto osób: {len(set(t.get('speaker') for t in turns if t.get('speaker')))}\n"
            f"Zaktualizowano plik:\n{os.path.basename(self.current_txt_path or '')}"
        )

    def _on_transcription_double_clicked(self, item):
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Odczytaj meeting_id z sesji JSON lub wylicz deterministyczny identyfikator
                json_path = get_session_path_for_txt(file_path)
                sess = TranscriptionSession.load_from_json(json_path) if os.path.exists(json_path) else None

                session_dt = None
                if sess and sess.created_at:
                    try:
                        session_dt = datetime.fromisoformat(sess.created_at)
                    except Exception:
                        pass
                if not session_dt:
                    session_dt = extract_datetime_from_filename(file_path)

                if sess and getattr(sess, "meeting_id", None):
                    self.current_meeting_id = sess.meeting_id
                elif sess and getattr(sess, "prepared_wav", None):
                    self.last_audio_save_path = sess.prepared_wav
                    stem = os.path.splitext(os.path.basename(sess.prepared_wav))[0].replace("inteligentne_nagranie_", "")
                    self.current_meeting_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"recorder67_{stem}"))
                else:
                    txt_stem = os.path.splitext(os.path.basename(file_path))[0].replace("transkrypcja_", "")
                    self.current_meeting_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"recorder67_{txt_stem}"))

                # 1. Preferuj oryginalne turns z pliku sesji JSON
                if sess and sess.turns:
                    self.current_turns = sess.turns
                    self._populate_speaker_mapping(sess.turns)
                    html_content = sess.export_to_html(session_start_time=session_dt)
                    self.text_transcript.setHtml(html_content)
                    self._scroll_transcript_view()
                else:
                    turns = parse_txt_to_turns(content, session_start_time=session_dt)
                    self.current_turns = turns or []
                    if turns:
                        self._populate_speaker_mapping(turns)
                        html_content, _ = format_turns(turns, session_start_time=session_dt)
                        self.text_transcript.setHtml(html_content)
                        self._scroll_transcript_view()
                    else:
                        self.speaker_box.setVisible(False)
                        from recorder.config import get_preview_order
                        lines = [l for l in content.split("\n") if l.strip()]
                        if get_preview_order() == "newest_first":
                            lines = list(reversed(lines))
                        html_content = "<br><br>".join(lines)
                        self.text_transcript.setHtml(html_content)
                        self._scroll_transcript_view()

                self.btn_manual_sync.setEnabled(True)
                target_name = self.cloud_sync.config.get("sync_target", "emanager").upper()
                self.lbl_cloud_status.setText(f"☁️ Wczytano plik: {os.path.basename(file_path)} (Gotowy do wysłania)")
                self.lbl_cloud_status.setStyleSheet("color: #4cc9f0; font-size: 11px; font-weight: bold;")
            except Exception as e:
                QMessageBox.warning(self, "Błąd Odczytu", f"Nie udało się otworzyć pliku:\n{e}")


    def _on_open_txt_folder_clicked(self):
        if os.path.exists(self.transcriptions_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.transcriptions_dir))

    def _on_transcription_error(self, err_msg):
        self.progress_transcription.setValue(0)
        self.progress_transcription.setFormat("Błąd transkrypcji!")
        QMessageBox.critical(self, "Błąd AI", f"Wystąpił błąd podczas przetwarzania:\n{err_msg}")
        self.btn_start.setEnabled(True)
        self.btn_upload.setEnabled(True)
        self.combo_devices.setEnabled(True)
        self.combo_models.setEnabled(True)
        self.btn_auto_detect.setEnabled(True)
        self.check_enable_diarization.setEnabled(True)
        is_diar = self.check_enable_diarization.isChecked()
        self.combo_speakers.setEnabled(is_diar)
        self.input_token.setEnabled(is_diar)

    def _on_timer_tick(self):
        # Precyzyjny czas nagrania (monotoniczny, bez dryfu i bez przeskakiwania sekund)
        is_active_recording = self.worker.state in [SmartRecordState.RECORDING_SPEECH, SmartRecordState.RECORDING_SILENCE_COUNTDOWN]
        now = time.monotonic()
        if is_active_recording:
            if getattr(self, "_last_active_tick", None) is not None:
                dt = now - self._last_active_tick
                if 0.0 < dt < 2.0:
                    self._active_recorded_time += dt
            self._last_active_tick = now
            self.recorded_seconds = int(self._active_recorded_time)

            hrs = self.recorded_seconds // 3600
            mins = (self.recorded_seconds % 3600) // 60
            secs = self.recorded_seconds % 60
            new_text = f"{hrs:02d}:{mins:02d}:{secs:02d}"
            if self.lbl_timer.text() != new_text:
                self.lbl_timer.setText(new_text)

            if getattr(self, "rolling_worker", None) is not None:
                self.rolling_worker.update_session_time(self.recorded_seconds)
                proc_sec = self.rolling_worker.total_processed_seconds
                if proc_sec > 0:
                    disp_proc_sec = min(float(self.recorded_seconds), proc_sec)
                    pct = int(min(98, max(5, (disp_proc_sec / max(1.0, float(self.recorded_seconds))) * 100)))
                    p_min, p_sec = int(disp_proc_sec // 60), int(disp_proc_sec % 60)
                    t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
                    self.progress_transcription.setValue(pct)
                    blk_str = f" · blok #{self.last_processed_block_idx}" if self.last_processed_block_idx > 0 else ""
                    self.progress_transcription.setFormat(f"🟢 Przetworzono w tle: {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d} ({pct}%{blk_str})")
                else:
                    # Informacja o aktywnym zbieraniu i buforowaniu mowy przed pierwszym blokiem
                    t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
                    if self.recorded_seconds < 12:
                        pct = int(min(80, max(5, (self.recorded_seconds / 12.0) * 80)))
                        self.progress_transcription.setValue(pct)
                        self.progress_transcription.setFormat(f"🎙️ Zbieranie mowy do pierwszego bloku: {t_min:02d}:{t_sec:02d}...")
                    else:
                        pct = min(92, 80 + int((self.recorded_seconds - 12) * 2))
                        self.progress_transcription.setValue(pct)
                        self.progress_transcription.setFormat(f"⚡ Przetwarzanie pierwszego fragmentu w tle: {t_min:02d}:{t_sec:02d}...")
        elif self.worker.state == SmartRecordState.AUTO_PAUSED:
            self._last_active_tick = None
            t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
            proc_sec = getattr(self.rolling_worker, "total_processed_seconds", 0.0) if getattr(self, "rolling_worker", None) else 0.0
            if proc_sec > 0:
                disp_proc_sec = min(float(self.recorded_seconds), proc_sec)
                p_min, p_sec = int(disp_proc_sec // 60), int(disp_proc_sec % 60)
                blk_str = f" · blok #{self.last_processed_block_idx}" if self.last_processed_block_idx > 0 else ""
                self.progress_transcription.setFormat(f"⏸️ Auto-Pauza (Cisza): {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d}{blk_str}")
            else:
                self.progress_transcription.setFormat(f"⏸️ Auto-Pauza (Cisza): {t_min:02d}:{t_sec:02d}")
        elif self.worker.state == SmartRecordState.MANUAL_PAUSED:
            self._last_active_tick = None
            t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
            proc_sec = getattr(self.rolling_worker, "total_processed_seconds", 0.0) if getattr(self, "rolling_worker", None) else 0.0
            if proc_sec > 0:
                disp_proc_sec = min(float(self.recorded_seconds), proc_sec)
                p_min, p_sec = int(disp_proc_sec // 60), int(disp_proc_sec % 60)
                blk_str = f" · blok #{self.last_processed_block_idx}" if self.last_processed_block_idx > 0 else ""
                self.progress_transcription.setFormat(f"⏸️ Wstrzymano ręcznie: {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d}{blk_str}")
            else:
                self.progress_transcription.setFormat(f"⏸️ Wstrzymano ręcznie: {t_min:02d}:{t_sec:02d}")

    def _update_audio_level(self, level):
        pass

    def _update_vad_info(self, is_speech, speech_prob, current_silence_sec):
        if self.worker.state == SmartRecordState.MANUAL_PAUSED:
            self.progress_silence.setValue(0)
            self.lbl_vad_detail.setText("⏸ Nagrywanie wstrzymane ręcznie (kliknij 'Wznów Nagrywanie', aby kontynuować)")
            self.lbl_vad_detail.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 11px;")
            return

        try:
            threshold = float(self.slider_silence.value())
            if current_silence_sec is None or current_silence_sec != current_silence_sec:
                current_silence_sec = 0.0
            val_tenths = int(max(0.0, min(float(current_silence_sec), threshold)) * 10)
            self.progress_silence.setValue(val_tenths)
            self.lbl_silence_val.setText(f"{float(current_silence_sec):.1f} s / {threshold:.1f} s")
        except Exception:
            self.progress_silence.setValue(0)

        vad_mode_str = "Silero VAD AI" if is_silero_available() else "Detekcja Energii"
        prob_pct = int(speech_prob * 100) if (speech_prob and speech_prob == speech_prob) else 0
        if is_speech:
            self.lbl_vad_detail.setText(f"🗣️ VAD: DETEKCJA MOWY ({prob_pct}% pewności AI, Tryb: {vad_mode_str})")
            self.lbl_vad_detail.setStyleSheet("color: #10b981; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_vad_detail.setText(f"🔇 VAD: Cisza / Szum tła ({prob_pct}% pewności AI, Tryb: {vad_mode_str})")
            self.lbl_vad_detail.setStyleSheet("color: #8d99ae; font-size: 11px;")

    def _on_worker_state_changed(self, state):
        thresh_val = self.slider_silence.value()
        if state == SmartRecordState.STOPPED:
            self.lbl_status_badge.setText("ZATRZYMANY")
            self.lbl_status_badge.setObjectName("StatusStopped")
            self.btn_pause.setText("⏸ Wstrzymaj Ręcznie")
            self.btn_pause.setObjectName("BtnPause")
            self._update_tray_tooltip("Gotowy")
        elif state == SmartRecordState.RECORDING_SPEECH:
            self.lbl_status_badge.setText("🟢 NAGRYWANIE (WYKRYTO MOWĘ)")
            self.lbl_status_badge.setObjectName("StatusSpeech")
            self.btn_pause.setText("⏸ Wstrzymaj Ręcznie")
            self.btn_pause.setObjectName("BtnPause")
            self._update_tray_tooltip("Nagrywanie trwa")
        elif state == SmartRecordState.RECORDING_SILENCE_COUNTDOWN:
            self.lbl_status_badge.setText("⏳ ODLICZANIE BRAKU MOWY (NAGRYWANIE)")
            self.lbl_status_badge.setObjectName("StatusCountdown")
            self.btn_pause.setText("⏸ Wstrzymaj Ręcznie")
            self.btn_pause.setObjectName("BtnPause")
            self._update_tray_tooltip("Nagrywanie trwa")
        elif state == SmartRecordState.AUTO_PAUSED:
            self.lbl_status_badge.setText(f"🟡 AUTOMATYCZNIE WSTRZYMANO (BRAK MOWY > {thresh_val}s)")
            self.lbl_status_badge.setObjectName("StatusAutoPaused")
            self.btn_pause.setText("⏸ Wstrzymaj Ręcznie")
            self.btn_pause.setObjectName("BtnPause")
            self._update_tray_tooltip("Wstrzymano (cisza)")
        elif state == SmartRecordState.MANUAL_PAUSED:
            self.lbl_status_badge.setText("⏸ WSTRZYMANO RĘCZNIE")
            self.lbl_status_badge.setObjectName("StatusManualPaused")
            self.btn_pause.setText("▶ Wznów Nagrywanie")
            self.btn_pause.setObjectName("BtnResume")
            self._update_tray_tooltip("Wstrzymano ręcznie")
            t_min, t_sec = int(self.recorded_seconds // 60), int(self.recorded_seconds % 60)
            proc_sec = getattr(self.rolling_worker, "total_processed_seconds", 0.0) if getattr(self, "rolling_worker", None) else 0.0
            if proc_sec > 0:
                p_min, p_sec = int(proc_sec // 60), int(proc_sec % 60)
                blk_str = f" · blok #{self.last_processed_block_idx}" if self.last_processed_block_idx > 0 else ""
                self.progress_transcription.setFormat(f"⏸️ Wstrzymano ręcznie: {p_min:02d}:{p_sec:02d} / {t_min:02d}:{t_sec:02d}{blk_str}")
            else:
                self.progress_transcription.setFormat(f"⏸️ Wstrzymano ręcznie: {t_min:02d}:{t_sec:02d}")

        self.lbl_status_badge.style().unpolish(self.lbl_status_badge)
        self.lbl_status_badge.style().polish(self.lbl_status_badge)
        self.lbl_status_badge.update()

        self.btn_pause.style().unpolish(self.btn_pause)
        self.btn_pause.style().polish(self.btn_pause)
        self.btn_pause.update()

    def _setup_tray_icon(self):
        """Inicjalizuje ikonę zasobnika systemowego Windows dla dyskretnych powiadomień."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        try:
            self.tray_icon = QSystemTrayIcon(self)
            from recorder.ui.windows_integration import get_app_icon_path
            ico_path = get_app_icon_path("ico")
            if ico_path and os.path.exists(ico_path):
                icon = QIcon(ico_path)
            else:
                icon = self.windowIcon()
            if icon.isNull():
                pix = QPixmap(32, 32)
                pix.fill(Qt.GlobalColor.transparent)
                p = QPainter(pix)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setBrush(QColor("#4361ee"))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(2, 2, 28, 28, 6, 6)
                p.setPen(QColor("#ffffff"))
                p.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
                p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "🎙")
                p.end()
                icon = QIcon(pix)
            self.tray_icon.setIcon(icon)
            self.tray_icon.setToolTip("Inteligentny Dyktafon AI — Gotowy")
            self.tray_icon.messageClicked.connect(self._on_tray_message_clicked)
            self.tray_icon.activated.connect(self._on_tray_icon_activated)

            # Menu podręczne pod prawym przyciskiem myszy
            tray_menu = QMenu(self)
            tray_menu.setStyleSheet("""
                QMenu {
                    background-color: #1e1e2f;
                    color: #edf2f4;
                    border: 1px solid #2b2d42;
                    border-radius: 6px;
                    padding: 4px;
                    font-family: "Segoe UI", sans-serif;
                    font-size: 12px;
                }
                QMenu::item {
                    padding: 6px 18px;
                    border-radius: 4px;
                }
                QMenu::item:selected {
                    background-color: #4361ee;
                    color: #ffffff;
                }
                QMenu::separator {
                    height: 1px;
                    background-color: #2b2d42;
                    margin: 4px 6px;
                }
            """)
            act_restore = tray_menu.addAction("🎙️ Otwórz okno")
            act_restore.triggered.connect(self._restore_from_tray)

            act_settings = tray_menu.addAction("⚙️ Ustawienia...")
            act_settings.triggered.connect(self._open_settings_dialog)

            tray_menu.addSeparator()
            act_quit = tray_menu.addAction("❌ Zakończ")
            act_quit.triggered.connect(self.close)

            self.tray_icon.setContextMenu(tray_menu)
            self.tray_icon.show()
        except Exception as e:
            print(f"[Tray] Nie udało się zainicjalizować ikony zasobnika: {e}")
            self.tray_icon = None

    def _on_tray_icon_activated(self, reason):
        """Obsługa kliknięcia ikony w zasobniku systemowym."""
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        """Przywraca i aktywuje okno główne z zasobnika."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _update_tray_tooltip(self, state_text: str = ""):
        """Aktualizuje opis ikony w zasobniku systemowym."""
        if getattr(self, "tray_icon", None) is not None:
            if state_text:
                self.tray_icon.setToolTip(f"Inteligentny Dyktafon AI — {state_text}")
            else:
                self.tray_icon.setToolTip("Inteligentny Dyktafon AI")

    def _on_tray_message_clicked(self):
        self._restore_from_tray()
        self._show_audio_inspection_dialog(self.combo_source_mode.currentData() or RecordSourceMode.HYBRID_DUAL)

    def _show_audio_inspection_dialog(self, source_mode: str):
        # 1. Przywrócenie okna głównego, aby użytkownik widział wskaźniki VU i urządzenia
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

        self._refresh_audio_devices()
        if source_mode == RecordSourceMode.SYSTEM_ONLY:
            msg_body = (
                "Dyktafon odświeżył listę urządzeń audio i aplikacji w systemie Windows.\n\n"
                "Zalecane kroki sprawdzające:\n"
                "1. Upewnij się, że wybrany program (np. Discord) faktycznie odtwarza dźwięk.\n"
                "2. Sprawdź, czy w polu «Aplikacja audio» wybrano właściwy program lub «Wszystkie programy».\n"
                "3. Upewnij się, że aplikacja nie została wyciszona w mikserze głośności Windows.\n"
                "4. W razie potrzeby kliknij «Stop i Zapisz» i rozpocznij nowe nagranie."
            )
        else:
            msg_body = (
                "Dyktafon odświeżył listę urządzeń audio w systemie Windows.\n\n"
                "Zalecane kroki sprawdzające:\n"
                "1. Sprawdź fizyczny przycisk MUTE na mikrofonie lub nadajniku bezprzewodowym.\n"
                "2. Upewnij się, że wybrany mikrofon na liście w programie jest poprawny.\n"
                "3. Jeśli mikrofon został odłączony lub zawieszony, kliknij «Stop i Zapisz», a następnie rozpocznij nowe nagranie."
            )

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setWindowTitle("Weryfikacja Urządzeń Audio")
        msg_box.setText(msg_body)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)

        # Precyzyjne wyśrodkowanie okna komunikatu na środku ekranu monitora
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            hint = msg_box.sizeHint()
            x = max(0, geom.center().x() - (hint.width() // 2))
            y = max(0, geom.center().y() - (hint.height() // 2))
            msg_box.move(x, y)

        msg_box.exec()

    def _handle_silence_confirmed(self, mins_str: str):
        if hasattr(self, "worker"):
            self.worker.reset_silence_alert()
        self.lbl_cloud_status.setText("✅ Nagrywanie trwa (aktywność potwierdzona).")
        self.lbl_cloud_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")
        self._update_tray_tooltip("Nagrywanie trwa")

    def _handle_silence_inspect_requested(self, source_mode: str):
        if hasattr(self, "worker"):
            self.worker.reset_silence_alert()
        self._update_tray_tooltip("Nagrywanie trwa")
        self._show_audio_inspection_dialog(source_mode)

    def show_silence_alert_preview(self, silence_sec: float):
        """Wyświetla próbkę powiadomienia na żądanie z okna ustawień."""
        src_mode = self.combo_source_mode.currentData() or RecordSourceMode.HYBRID_DUAL
        self._on_silence_alert(silence_sec, src_mode)

    def _on_silence_alert(self, silence_sec: float, source_mode: str):
        """Obsługuje sygnał strażnika ciszy z wątku SmartAudioWorker lub testu ustawień."""
        mins = int(silence_sec // 60)
        mins_str = f"{mins} min" if mins > 0 else f"{int(silence_sec)} s"

        if getattr(self, "_active_silence_toast", None) is not None:
            try:
                self._active_silence_toast.close()
            except Exception:
                pass

        if hasattr(self, "worker") and self.worker is not None:
            self.worker.suppress_sys_audio_for(0.8)

        toast = SilenceToastBanner(self, silence_sec=silence_sec, source_mode=source_mode)
        self._active_silence_toast = toast
        toast.confirmed.connect(lambda: self._handle_silence_confirmed(mins_str))
        toast.inspect_requested.connect(lambda: self._handle_silence_inspect_requested(source_mode))
        toast.dismissed.connect(lambda: self._handle_silence_confirmed(mins_str))
        toast.timed_out.connect(lambda: self._handle_silence_timed_out_to_tray(mins_str, source_mode))
        toast.show()

    def _handle_silence_timed_out_to_tray(self, mins_str: str, source_mode: str):
        """Gdy nikt nie kliknął toasta przez 45s (nieobecność), przekazuje powiadomienie do Centrum Akcji Windows."""
        if hasattr(self, "worker"):
            self.worker.reset_silence_alert()
        title = f"⚠️ Brak dźwięku od {mins_str}"
        msg = f"Dyktafon rejestruje czas, ale nie wykryto mowy ani dźwięku.\nKliknij tutaj, aby sprawdzić stan urządzeń."
        if getattr(self, "tray_icon", None) is not None:
            self._update_tray_tooltip(f"Brak dźwięku ({mins_str})")
            self.tray_icon.showMessage(
                title,
                msg,
                QSystemTrayIcon.MessageIcon.Warning,
                15000
            )
        self.lbl_cloud_status.setText(f"⚠️ Brak dźwięku od {mins_str}.")
        self.lbl_cloud_status.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: bold;")

    def _handle_audio_error(self, err_msg):
        self._on_stop_clicked()
        QMessageBox.critical(
            self,
            "Urządzenie Audio",
            f"{err_msg}\n\n"
            "Upewnij się, że mikrofon jest podłączony w Windows i sprawny."
        )

    def _on_recording_double_clicked(self, item):
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path and os.path.exists(file_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _on_open_folder_clicked(self):
        if os.path.exists(self.recordings_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.recordings_dir))

    def closeEvent(self, event):
        try:
            self.timer.stop()

            # 1. Zatrzymanie wszystkich zarejestrowanych wątków w self._active_threads
            for th in list(self._active_threads):
                try:
                    th.blockSignals(True)
                    if hasattr(th, "stop"):
                        th.stop()
                    if hasattr(th, "quit"):
                        th.quit()
                    th.wait(2000)
                except Exception:
                    pass
            self._active_threads.clear()

            # 2. Zablokowanie sygnałów i zatrzymanie wątku audio oraz zapis audio
            if self.worker is not None:
                try:
                    self.worker.blockSignals(True)
                except Exception:
                    pass
                if self.worker.isRunning() or self.worker.state != SmartRecordState.STOPPED:
                    timestamp = getattr(self, "current_live_timestamp", datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
                    save_path = getattr(self, "current_live_wav_path", None) or os.path.join(self.recordings_dir, f"inteligentne_nagranie_{timestamp}.wav")
                    self.worker.stop_recording()
                    self.worker.wait(3000)
                    try:
                        self.worker.save_wav(save_path)
                    except Exception as e:
                        import logging
                        logging.getLogger("recorder").error(f"Błąd zapisu pliku audio WAV podczas zamykania aplikacji ('{save_path}'): {e}")
                elif self.worker.isRunning():
                    self.worker.wait(3000)

            # 3. Pozostałe wątki pomocnicze
            if getattr(self, "live_transcription_worker", None) is not None:
                try:
                    self.live_transcription_worker.blockSignals(True)
                except Exception:
                    pass
                if self.live_transcription_worker.isRunning():
                    self.live_transcription_worker.stop()
                    self.live_transcription_worker.wait(1500)

            if getattr(self, "transcription_thread", None) is not None:
                try:
                    self.transcription_thread.blockSignals(True)
                except Exception:
                    pass
                if self.transcription_thread.isRunning():
                    self.transcription_thread.quit()
                    self.transcription_thread.wait(1500)

            if getattr(self, "file_processing_worker", None) is not None:
                try:
                    self.file_processing_worker.blockSignals(True)
                except Exception:
                    pass
                if self.file_processing_worker.isRunning():
                    self.file_processing_worker.quit()
                    self.file_processing_worker.wait(1500)

            if getattr(self, "_startup_update_worker", None) is not None:
                try:
                    self._startup_update_worker.blockSignals(True)
                    if self._startup_update_worker.isRunning():
                        self._startup_update_worker.quit()
                        self._startup_update_worker.wait(1000)
                except Exception:
                    pass

            if getattr(self, "_active_silence_toast", None) is not None:
                try:
                    self._active_silence_toast.close()
                except Exception:
                    pass

            if getattr(self, "tray_icon", None) is not None:
                try:
                    self.tray_icon.hide()
                    self.tray_icon.deleteLater()
                except Exception:
                    pass

            # 4. Sprawdzenie oczekującej aktualizacji przy wyjściu (Install on exit)
            pending_zip = getattr(self, "_pending_update_zip_path", None)
            if pending_zip and os.path.exists(pending_zip):
                is_frozen = getattr(sys, "frozen", False)
                if is_frozen:
                    from recorder.core.updater import apply_in_place_update
                    print(f"[UPDATER] Uruchamianie instalacji w tle przy wyjściu: {pending_zip}")
                    apply_in_place_update(pending_zip, restart_after=False)

        except Exception as e:
            print(f"[closeEvent] Błąd zamykania okna: {e}")
        finally:
            event.accept()
