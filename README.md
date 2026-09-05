# 🎙️ Recorder67 - Asystent Biurowy Ambient AI

Nowoczesny system ciągłego monitorowania mowy w biurze, lokalnej transkrypcji AI (offline), detekcji aktywności głosu (VAD) oraz asynchronicznej synchronizacji z systemem CRM / bazą Supabase.

Szczegółowy opis założeń architektonicznych, pamięci projektu oraz statusu realizacji znajduje się w dokumencie:
👉 **[PROJECT_GOAL.md](PROJECT_GOAL.md)**

---

## ✨ Główne Funkcjonalności

* 🎙️ **Detekcja Aktywności Głosu (Silero VAD AI):** Wykrywanie mowy w czasie rzeczywistym, bufor pre-padding (brak ucinania pierwszych głosek), konfigurowalny czas auto-pauzy oraz auto-wznawianie.
* 🎧 **Nagrywanie Hybrydowe (Mikrofon + Dźwięk Systemu / WASAPI Loopback):** Niezależne lub jednoczesne rejestrowanie mowy z mikrofonu oraz dźwięku spotkań online (Discord, Teams, Zoom) z możliwością izolacji wybranego procesu audio oraz szybkimi przyciskami wyciszenia MUTE.
* ⚡ **Transkrypcja na Żywo w Tle (Rolling Transcriber):** Ciągłe przetwarzanie wypowiedzi w tle za pomocą `faster-whisper` (modele `small`, `medium`, `large-v3-turbo`) z akceleracją GPU (CUDA float16) lub CPU (int8).
* 🛡️ **Zaawansowane Filtry Anty-Halucynacyjne:** Algorytmiczne usuwanie patologicznych pętli powtórzeń (1-gramów i 2-gramów, np. zacięć śmiechu, oddechów czy wielokrotnych powtórzeń), z zachowaniem pełnej treści wartościowych zdań.
* 👥 **Separacja i Autosugestia Mówców:** Opcjonalna diaryzacja `pyannote.audio` (`speaker-diarization-3.1`) z panelem autosugestii imion na podstawie kontekstu rozmów.
* 🔔 **Inteligentne Ostrzeganie o Braku Dźwięku:** Dyskretny baner w stylu Windows 11 Fluent z szybkimi akcjami (*«Wszystko gra»* / *«Sprawdź dźwięk»*) oraz automatycznym przekazywaniem do Centrum Akcji Windows z priorytetem alarmu (przebijającym tryb *Nie przeszkadzać*) po 45s nieobecności.
* 🪟 **Natywna Integracja z Windows & Tray:** Tożsamość procesu `InteligentnyDyktafonAI`, dedykowana ikona Fluent, dynamiczny zasobnik systemowy (Tray) z menu podręcznym i przywracaniem okna lewym klikiem.
* ☁️ **Agnostyczna Synchronizacja Chmurowa (Cloud Sync):** Transmisja segmentów transkrypcji na żywo do bazy Supabase / REST API / Webhooka CRM z trwałymi identyfikatorami UUID, buforem ponawiania prób bez utraty danych, bezkonfliktowym scalaniem i kolejką offline.
* ⏱️ **Inteligentny Podział Sesji (Smart Session Splitting):** Automatyczne domykanie bieżącego spotkania po konfigurowalnym czasie ciszy (np. 15 minut) i płynne rozpoczynanie nowej sesji bez przerywania nasłuchu.
* 💾 **Optymalizacja Pamięci RAM i Skalowalność 8h:** Stałe zużycie pamięci $O(1)$ w maratonach nagraniowych (4h–8h), automatyczna kompensacja asymetrii buforów audio, inteligentny throttling zapisu dyskowego I/O (zmniejszenie obciążenia SSD o 95%) oraz strumieniowy zapis dźwięku na dysk (`StreamingWavWriter`).
* 🚀 **Adaptacyjny Whisper Turbo:** Dynamiczny dobór `beam_size=1` przy spiętrzeniu bloków mowy w kolejce dla natychmiastowego odciążenia procesora, z zachowaniem maksymalnej precyzji `beam_size=5` w standardowych warunkach.
* 🎙️ **Pętla Retry WASAPI dla Urządzeń Bluetooth:** Automatyczne wznawianie strumieni audio przy szybkim zatrzymaniu/wznowieniu nagrań, eliminujące błędy rozłączenia urządzeń bezprzewodowych.
* 🚀 **Nowoczesny Auto-Updater (WinForms GUI) & Bogaty Changelog Markdown:** Graficzny pasek postępu instalacji aktualizacji w locie, obsługa pełnego formatowania Markdown w oknie Ustawień, automatyczna agregacja pominiętych wydań oraz przeglądarka historii wersji.
* 🛡️ **Wydanie Produkcyjne bez Konsoli & Centralne Logowanie:** Aplikacja działa jako czyste okno Windows (`--noconsole`) z rotującym dziennikiem zdarzeń `logs/app.log` (do 60 MB) oraz 100% automatycznym maskowaniem danych poufnych (klucze API, hasła, webhooki).
* 🎨 **System Motywów Graficznych i Personalizacja UI:** 4 kompletne motywy (Classic Dark, Classic Light, EMANAGER Dark, EMANAGER Light) z interaktywnymi kartami podglądu w zakładce „Wygląd & Personalizacja", regulacją rozmiaru czcionki transkrypcji oraz natywną integracją z trybem ciemnym/jasnym paska tytułowego Windows (DWM).
* 📌 **Zaawansowane Zachowania Okna:** Przełącznik „Zawsze na wierzchu" (Always on Top), minimalizacja do zasobnika systemowego przy kliknięciu [X] (ochrona długich sesji nagraniowych) oraz pamięć rozmiaru i pozycji okna między uruchomieniami.

---

## 🚀 Szybki Start

### 1. Wymagania wstępne
* Python 3.10+ (zalecany Python 3.10 lub 3.11 na Windows)
* Karta NVIDIA z obsługą CUDA (opcjonalnie dla akceleracji GPU) lub wielordzeniowy procesor CPU.

### 2. Instalacja zależności
```bash
pip install -r requirements.txt
```

### 3. Konfiguracja środowiska (.env)
Skopiuj plik `.env.example` do `.env` i uzupełnij wymagane klucze:
```bash
copy .env.example .env
```

W pliku `.env`:
```env
# Hugging Face Token (wymagany tylko do diaryzacji PyAnnote)
HF_TOKEN=hf_twoj_token_tutaj

# Konfiguracja synchronizacji chmurowej (Supabase / CRM)
SYNC_TARGET=emanager
SUPABASE_URL=https://twoj-projekt.supabase.co
SUPABASE_KEY=twoj_anon_lub_publishable_key
ORGANIZATION_ID=default_org
DEVICE_NAME=Biuro-Stanowisko-1
AUTO_CLOUD_SYNC=true
```

### 4. Uruchomienie aplikacji
```bash
python main.py
```

---

## 🏗️ Budowanie wersji instalacyjnej (.EXE)

Aplikację można skompilować do samodzielnego pliku wykonywalnego dla systemu Windows:

```powershell
# Uruchom dedykowany skrypt budowania PyInstaller:
.\build_exe.ps1
```
Gotowy plik `.exe` wraz ze wszystkimi zależnościami zostanie utworzony w katalogu `dist/InteligentnyDyktafonAI/`.

---

## 📁 Struktura Projektu

```text
recorder67/
├── .github/workflows/          # CI/CD GitHub Actions (automatyczny build Windows EXE i publikacja wydań)
│   └── release.yml
├── main.py                     # Główny punkt startowy aplikacji
├── .env.example                # Przykładowy szablon konfiguracji środowiska
├── PROJECT_GOAL.md             # Pamięć projektu, roadmapa i architektura
├── requirements.txt            # Zależności Python (PySide6, faster-whisper, torch, torchaudio, itp.)
├── InteligentnyDyktafonAI.spec # Specyfikacja kompilacji PyInstaller
├── build_exe.ps1               # Skrypt automatycznego budowania EXE
│
└── recorder/
    ├── config.py               # Centralna konfiguracja, stałe i ustawienia użytkownika
    │
    ├── core/                   # Logika przetwarzania audio i modeli AI
    │   ├── vad.py              # Detektor aktywności głosu Silero VAD
    │   ├── transcriber.py      # Silnik Faster-Whisper, filtry halucynacji i deduplikacja
    │   ├── rolling_transcriber.py # Asynchroniczny transkryber blokowy na żywo
    │   ├── diarizer.py         # Silnik diaryzacji PyAnnote (Speaker Diarization)
    │   ├── speakers.py         # Analiza dialogów i autosugestia imion mówców
    │   ├── session.py          # Zarządzanie strukturą i zapisem sesji JSON/TXT
    │   ├── cloud_sync.py       # Asynchroniczna synchronizacja chmurowa i kolejka offline
    │   ├── logger.py           # Centralne rotujące logowanie, przekierowanie strumieni i maskowanie danych
    │   └── updater.py          # Auto-updater (WinForms GUI, GitHub Releases API i podmiana in-place)
    │
    ├── audio/                  # Obsługa wejść audio i operacji na plikach
    │   ├── devices.py          # Wykrywanie i filtrowanie mikrofonów (DirectSound/WASAPI/MME)
    │   ├── converter.py        # Resampling do 16 kHz, filtry pasmowe i normalizacja
    │   └── capture.py          # Strumieniowy zapis WAV na dysku (StreamingWavWriter)
    ├── resources/              # Zasoby aplikacji (ikona app_icon.ico / .png, fonty Saira OFL)
    │
    └── ui/                     # Interfejs graficzny użytkownika (PySide6)
        ├── window.py           # Główne okno aplikacji (SmartDictaphoneWindow)
        ├── workers.py          # Wątki robocze audio i transkrypcji (QThread)
        ├── settings_dialog.py  # Okno ustawień słownika, parametrów VAD i podziału sesji
        ├── appearance_tab.py   # Zakładka „Wygląd & Personalizacja" (motywy, czcionka, zachowania okna)
        ├── windows_integration.py # Tożsamość procesu Windows, AUMID i natywne toasty
        └── theme.py            # Centralny silnik motywów QSS (4 motywy, fonty Saira, integracja DWM)

```
