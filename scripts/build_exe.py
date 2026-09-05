"""
Skrypt budowania aplikacji Inteligentnego Dyktafonu AI do wersji .EXE (Windows).
Użycie:
    python scripts/build_exe.py
"""

import os
import sys
import subprocess
import shutil
import importlib.metadata
import importlib.util

# Wymuś kodowanie UTF-8 na stdout/stderr w konsoli Windows (np. runner GitHub Actions)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_POINT = os.path.join(ROOT_DIR, "run.py")
DIST_DIR = os.path.join(ROOT_DIR, "dist")
BUILD_DIR = os.path.join(ROOT_DIR, "build")
LOG_FILE = os.path.join(ROOT_DIR, "build_log.txt")


class TeeLogger:
    """Duplikuje strumień wyjścia (stdout/stderr) jednocześnie na ekran konsoli oraz do pliku tekstowego."""
    def __init__(self, filepath: str, original_stream):
        self.file = open(filepath, "w", encoding="utf-8", errors="replace")
        self.original_stream = original_stream

    def write(self, data):
        try:
            self.original_stream.write(data)
        except UnicodeEncodeError:
            enc = getattr(self.original_stream, "encoding", "ascii") or "ascii"
            safe_data = data.encode(enc, errors="replace").decode(enc, errors="replace")
            self.original_stream.write(safe_data)
        except Exception:
            pass
        self.original_stream.flush()
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.original_stream.flush()
        self.file.flush()

    def close(self):
        try:
            self.file.close()
        except Exception:
            pass


def _is_module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def get_site_packages_modules() -> list[str]:
    """Pobiera listę wszystkich modułów najwyższego poziomu zainstalowanych w site-packages."""
    modules = set()
    for p in sys.path:
        if "site-packages" in p and os.path.exists(p):
            try:
                for item in os.listdir(p):
                    if item.endswith(".dist-info") or item.endswith(".egg-info") or item.startswith("__") or item.startswith("."):
                        continue
                    name = item
                    if item.endswith(".py"):
                        name = item[:-3]
                    elif "." in item:
                        continue
                    # Wykluczenia narzędzi deweloperskich i konfliktowych bibliotek
                    if name.lower() not in ("pyqt6", "pyqt6_sip", "pip", "setuptools", "wheel", "pyinstaller", "pefile", "altgraph"):
                        modules.add(name)
            except Exception:
                pass
    return sorted(list(modules))


def main():
    # Inicjalizacja automatycznego zapisu logu do pliku build_log.txt
    tee = TeeLogger(LOG_FILE, sys.stdout)
    sys.stdout = tee
    sys.stderr = tee

    print("=" * 70)
    print("🚀 BUDOWANIE INTELIGENTNEGO DYKTAFONU AI DO PLIKU .EXE")
    print("=" * 70)
    print(f"📄 Logi kompilacji są na bieżąco zapisywane do: {LOG_FILE}\n")

    # 1. Odblokowanie plików binarnych przed blokadą Windows Smart App Control
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "if (Test-Path 'env') { Get-ChildItem -Path 'env' -Recurse | Unblock-File }"],
            cwd=ROOT_DIR,
            capture_output=True
        )
    except Exception:
        pass

    try:
        import PyInstaller
        print(f"✅ Znaleziono PyInstaller w wersji: {PyInstaller.__version__}")
    except ImportError:
        print("📦 Instalowanie PyInstaller w środowisku...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 2. Automatyczne wykrycie WSZYSTKICH pakietów i metadanych w środowisku env
    all_dists = []
    try:
        all_dists = sorted(list(set([
            dist.metadata['Name']
            for dist in importlib.metadata.distributions()
            if dist.metadata and 'Name' in dist.metadata
        ])))
        print(f"📦 Automatycznie wykryto {len(all_dists)} pakietów w środowisku Python.")
    except Exception as e:
        print(f"⚠️ Ostrzeżenie przy skanowaniu pakietów: {e}")

    # 3. Wykrycie wszystkich modułów zainstalowanych w site-packages
    site_modules = get_site_packages_modules()
    print(f"📚 Automatycznie zebrano {len(site_modules)} modułów z site-packages do spakowania.")

    # Kluczowe biblioteki AI wymagające pełnego pakowania (wraz z plikami danych i bibliotekami C/C++)
    core_ai_collect = [
        "faster_whisper",
        "silero_vad",
        "pyannote",
        "pyannote.audio",
        "pyannote.core",
        "pyannote.pipeline",
        "pyannote.metrics",
        "pyannote.database",
        "asteroid_filterbanks",
        "julius",
        "torch_audiomentations",
        "torch_pitch_shift",
        "hyperpyyaml",
        "omegaconf",
        "einops",
        "semver",
        "sentencepiece",
        "pytorch_metric_learning",
        "ctranslate2",
        "sounddevice",
        "imageio_ffmpeg",
        "speechbrain",
        "lightning",
        "lightning_fabric",
        "lightning_utilities",
        "pytorch_lightning",
        "torchmetrics",
        "safetensors",
        "huggingface_hub",
        "optuna",
        "torch",
        "torchaudio",
        "onnxruntime",
        "scipy"
    ]

    # Połącz moduły AI oraz moduły z site-packages
    modules_to_collect = sorted(list(set([pkg for pkg in core_ai_collect if _is_module_available(pkg.split('.')[0])])))

    # Weryfikacja obecności zasobów czcionek (Saira SIL OFL)
    fonts_dir = os.path.join(ROOT_DIR, "recorder", "resources", "fonts")
    has_fonts = False
    if os.path.isdir(fonts_dir):
        font_files = [f for f in os.listdir(fonts_dir) if f.lower().endswith((".ttf", ".otf"))]
        if font_files:
            has_fonts = True
            print(f"🔤 Znaleziono pakiet czcionek: {fonts_dir} ({len(font_files)} plików: {', '.join(font_files)})")

    if not has_fonts:
        print("⚠️ Brak plików czcionek w recorder/resources/fonts! Próba automatycznego pobrania...")
        try:
            download_script = os.path.join(ROOT_DIR, "scripts", "download_fonts.py")
            if os.path.exists(download_script):
                subprocess.run([sys.executable, download_script], check=True, cwd=ROOT_DIR)
                print("✅ Automatycznie pobrano brakujące czcionki Saira.")
        except Exception as e:
            print(f"⚠️ Nie udało się automatycznie pobrać czcionek: {e}")

    # 4. Przygotuj parametry PyInstallera
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=InteligentnyDyktafonAI",
        "--onedir",                       # Folder z plikami DLL (najstabilniejszy dla modeli PyTorch/Whisper)
        "--clean",
        "--noconfirm",
        "--noconsole",                    # Wersja produkcyjna uruchamia się bez okna konsoli
        
        # Wykluczamy PyQt6, ponieważ aplikacja używa PySide6 (zapobiega konfliktom dwóch bibliotek Qt)
        "--exclude-module=PyQt6",
        "--exclude-module=PyQt6.QtCore",
        "--exclude-module=PyQt6.QtWidgets",
        "--exclude-module=PyQt6.QtGui",
        "--exclude-module=PyQt6_sip",

        # Zbieranie zależności i bibliotek C++/DLL dla wszystkich modułów AI
        *[
            f"--collect-all={pkg}"
            for pkg in modules_to_collect
        ],

        # Dołączenie ukrytych importów, które mogą być ładowane dynamicznie przez HuggingFace/PyTorch Hub
        *[
            f"--hidden-import={pkg}"
            for pkg in [
                "asteroid_filterbanks",
                "julius",
                "torch_audiomentations",
                "torch_pitch_shift",
                "hyperpyyaml",
                "omegaconf",
                "einops",
                "semver",
                "sentencepiece",
                "pytorch_metric_learning",
                "safetensors",
                "optuna"
            ]
            if _is_module_available(pkg)
        ],
        
        # Automatyczne dołączenie metadanych dla 100% wykrytych pakietów w środowisku!
        *[
            f"--copy-metadata={dist_name}"
            for dist_name in all_dists
            if not dist_name.lower().startswith("pyqt6")
        ],
        
        # Bezpieczne dołączenie pliku przykładowego .env.example (zamiast prywatnego .env)
        f"--add-data={os.path.join(ROOT_DIR, '.env.example')};." if os.path.exists(os.path.join(ROOT_DIR, '.env.example')) else "",

        # Dołączenie oficjalnej ikony i zasobów aplikacji (w tym czcionek Saira w recorder/resources/fonts/)
        f"--icon={os.path.join(ROOT_DIR, 'recorder', 'resources', 'app_icon.ico')}" if os.path.exists(os.path.join(ROOT_DIR, 'recorder', 'resources', 'app_icon.ico')) else "",
        f"--add-data={os.path.join(ROOT_DIR, 'recorder', 'resources')};recorder/resources" if os.path.exists(os.path.join(ROOT_DIR, 'recorder', 'resources')) else "",
        f"--add-data={os.path.join(ROOT_DIR, 'recorder', 'resources', 'fonts')};recorder/resources/fonts" if os.path.exists(os.path.join(ROOT_DIR, 'recorder', 'resources', 'fonts')) else "",
        
        ENTRY_POINT
    ]

    # Usuń puste argumenty
    cmd = [arg for arg in cmd if arg]

    print("\n⏳ Rozpoczynanie kompilacji PyInstaller (może to zająć 2-4 minuty)...")
    result = subprocess.run(cmd, cwd=ROOT_DIR)

    if result.returncode == 0:
        output_folder = os.path.join(DIST_DIR, "InteligentnyDyktafonAI")
        exe_path = os.path.join(output_folder, "InteligentnyDyktafonAI.exe")
        
        # Bezpieczne skopiowanie czystego .env.example oraz README do folderu wyjściowego
        example_src = os.path.join(ROOT_DIR, ".env.example")
        example_dst = os.path.join(output_folder, ".env.example")
        if os.path.exists(example_src):
            shutil.copy(example_src, example_dst)
            print("✅ Skopiowano czysty szablon konfiguracyjny .env.example do folderu aplikacji.")

        readme_src = os.path.join(ROOT_DIR, "README.md")
        readme_dst = os.path.join(output_folder, "README.md")
        if os.path.exists(readme_src):
            shutil.copy(readme_src, readme_dst)

        # Usunięcie ewentualnego prywatnego .env z folderu dist, jeśli zostałby przypadkowo skopiowany
        private_env = os.path.join(output_folder, ".env")
        if os.path.exists(private_env):
            try:
                os.remove(private_env)
                print("🔒 Usunięto prywatny plik .env z paczki produkcyjnej (dla bezpieczeństwa release'u).")
            except Exception:
                pass

        print("\n" + "=" * 70)
        print("🎉 SUKCES! Aplikacja została pomyślnie skompilowana!")
        print("=" * 70)
        print(f"📁 Folder aplikacji: {output_folder}")
        print(f"▶️ Plik startowy:    {exe_path}")
        print(f"📄 Pełny log kompilacji zapisano do: {LOG_FILE}")
        print("\n💡 Aby przygotować Release na GitHub:")
        print(f"   Zzipuj cały folder '{os.path.basename(output_folder)}' z katalogu 'dist/' i dodaj plik .ZIP do Release na GitHubie.")
    else:
        print("\n❌ Błąd podczas budowania pliku .exe.")
        print(f"📄 Pełny raport błędu znajduje się w pliku: {LOG_FILE}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
