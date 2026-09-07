"""
Moduł automatycznych aktualizacji (Auto-Updater) z GitHub Releases dla recorder67.
Obsługuje sprawdzanie wydań stabilnych i pre-release (alpha/beta), asynchroniczne pobieranie
oraz automatyczną podmianę plików aplikacji (in-place update) na Windowsie.
"""

import os
import sys
import re
import json
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, Tuple
from PySide6.QtCore import QThread, Signal as pyqtSignal

from recorder.config import APP_VERSION, GITHUB_REPO


def parse_version(v_str: str) -> Tuple[int, int, int, int, str]:
    """
    Parsuje wersję w formacie semver (np. 'v0.3.0-alpha', '0.4.0', 'v0.2.1.2')
    do krotki umożliwiającej rzetelne porównywanie wersji.
    
    Zwraca (major, minor, patch, prerelease_rank, prerelease_suffix).
    """
    if not v_str:
        return (0, 0, 0, 0, "")
        
    clean_v = v_str.strip().lstrip("vV")
    
    # Rozdzielenie części głównej od przyrostka pre-release
    prerelease_suffix = ""
    prerelease_rank = 100  # Wersja stabilna (brak przyrostka) ma najwyższy priorytet
    
    if "-" in clean_v:
        main_part, prerelease_suffix = clean_v.split("-", 1)
        suffix_lower = prerelease_suffix.lower()
        if "alpha" in suffix_lower:
            prerelease_rank = 10
        elif "beta" in suffix_lower:
            prerelease_rank = 20
        elif "rc" in suffix_lower:
            prerelease_rank = 30
        else:
            prerelease_rank = 15
    else:
        main_part = clean_v
        
    digits = []
    for part in re.findall(r'\d+', main_part):
        try:
            digits.append(int(part))
        except ValueError:
            pass
            
    while len(digits) < 3:
        digits.append(0)
        
    return (digits[0], digits[1], digits[2], prerelease_rank, prerelease_suffix)


def is_newer_version(remote_tag: str, local_version: str = APP_VERSION) -> bool:
    """Zwraca True, jeśli remote_tag reprezentuje nowszą wersję niż local_version."""
    remote_parsed = parse_version(remote_tag)
    local_parsed = parse_version(local_version)
    
    # 1. Porównanie numerów wersji major.minor.patch
    if remote_parsed[:3] > local_parsed[:3]:
        return True
    elif remote_parsed[:3] < local_parsed[:3]:
        return False
        
    # 2. Przy równych numerach głównych (np. 0.3.0 vs 0.3.0-alpha), wersja stabilna jest nowsza od alpha
    return remote_parsed[3] > local_parsed[3]


def fetch_all_releases(
    repo: str = GITHUB_REPO,
    include_prereleases: bool = True,
    timeout: int = 8,
    token: Optional[str] = None,
    raise_for_error: bool = False
) -> list[Dict[str, Any]]:
    """
    Pobiera pełną listę wydań z API GitHuba (posortowaną od najnowszych).
    Zwraca listę słowników opisujących każde wydanie.
    """
    url = f"https://api.github.com/repos/{repo}/releases"
    headers = {
        "User-Agent": f"Recorder67-App/{APP_VERSION}",
        "Accept": "application/vnd.github.v3+json"
    }
    github_token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    req = urllib.request.Request(url, headers=headers)
    releases = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return []
            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, list):
                return []

            for release in data:
                if release.get("draft", False):
                    continue
                is_prerelease = release.get("prerelease", False)
                if is_prerelease and not include_prereleases:
                    continue

                tag_name = release.get("tag_name", "")
                if not tag_name:
                    continue

                # Szukanie assetu ZIP z aplikacją
                assets = release.get("assets", [])
                zip_asset = None
                for a in assets:
                    a_name = a.get("name", "").lower()
                    if a_name.endswith(".zip"):
                        zip_asset = a
                        break

                raw_notes = release.get("body") or "Brak opisu zmian."
                clean_notes = sanitize_changelog_markdown(raw_notes)

                releases.append({
                    "tag_name": tag_name,
                    "release_title": release.get("name") or tag_name,
                    "release_notes": clean_notes,
                    "published_at": release.get("published_at", ""),
                    "is_prerelease": is_prerelease,
                    "html_url": release.get("html_url", ""),
                    "download_url": zip_asset.get("browser_download_url") if zip_asset else None,
                    "asset_name": zip_asset.get("name") if zip_asset else None,
                    "asset_size": zip_asset.get("size", 0) if zip_asset else 0,
                })
    except Exception as err:
        print(f"[UPDATER] Błąd pobierania listy wydań GitHub: {err}")
        if raise_for_error:
            raise

    return releases


def sanitize_changelog_markdown(text: Optional[str]) -> str:
    """
    Czyści i normalizuje tekst Markdown dla changelogu wydań:
    1. Usuwa ewentualne wycieki wewnętrznych styli CSS Qt (np. bloki <style> lub reguły 'p, li { white-space: pre-wrap; } ...').
    2. Naprawia uszkodzone/urwane linki porównawcze GitHub compare (np. [url](url)...v0.5.5).
    3. Opakowuje surowe linki GitHub compare w nawiasy ostrokątne <url>,
       dzięki czemu parser CommonMark / QTextBrowser w Qt nie urywa adresu na kropkach '...'
       traktowanych jako interpunkcja kończąca słowo, zapobiegając jednocześnie wchłanianiu
       interpunkcji kończącej zdanie (kropka, przecinek itp.) do wnętrza linku.
    """
    if not text:
        return ""

    s = str(text)

    # 1. Usuń ewentualne bloki tagów <style> oraz wstrzyknięte fragmenty styli CSS Qt
    s = re.sub(r'<style[^>]*>.*?</style>', '', s, flags=re.DOTALL)
    s = re.sub(
        r'p,\s*li\s*\{\s*white-space:[^\}]+\}\s*hr\s*\{\s*[^}]+\}\s*li\.unchecked::marker[^\}]+\}\s*li\.checked::marker[^\}]+\}',
        '',
        s,
        flags=re.DOTALL
    )

    # 2. Napraw uszkodzone linki markdown postaci [url/compare/tag1](url/compare/tag1)...tag2
    s = re.sub(
        r'\[([^\]]+)\]\((https?://github\.com/[^/\s]+/[^/\s]+/compare/[^\)\s]+)\)\.\.\.([a-zA-Z0-9\._-]*[a-zA-Z0-9_-])',
        r'<\2...\3>',
        s
    )

    # 3. Opakuj surowe linki GitHub compare w <...>, jeśli nie są już w <...> lub (...)
    # Upewnij się, że znak kończący tag to znak alfanumeryczny/łącznik, a nie kropka/przecinek kończący zdanie
    s = re.sub(
        r'(?<![<\(\[\"\'\=])(https?://github\.com/[^/\s]+/[^/\s]+/compare/[a-zA-Z0-9\._-]+\.\.\.[a-zA-Z0-9\._-]*[a-zA-Z0-9_-])(?![>\)\]\"\'\=])',
        r'<\1>',
        s
    )

    return s.strip()


def build_aggregated_changelog(newer_releases: list[Dict[str, Any]]) -> str:
    """
    Tworzy przejrzysty, zsumowany opis zmian w formacie Markdown dla wszystkich wydań,
    o które użytkownik jest do tyłu (od najnowszej do najstarszej brakującej).
    """
    if not newer_releases:
        return ""

    if len(newer_releases) == 1:
        return sanitize_changelog_markdown(str(newer_releases[0].get("release_notes") or "Brak opisu zmian."))

    blocks = []
    blocks.append(
        f"> ℹ️ **Uwaga:** Jesteś o **{len(newer_releases)}** wydań do tyłu. "
        "Poniżej znajduje się pełne zestawienie wszystkich brakujących nowości i poprawek:\n"
    )

    for idx, rel in enumerate(newer_releases, 1):
        tag = rel.get("tag_name", "")
        title = rel.get("release_title", tag)
        pub_date = rel.get("published_at", "")[:10]
        notes = sanitize_changelog_markdown(str(rel.get("release_notes") or "Brak opisu zmian.")).strip()
        date_str = f" ({pub_date})" if pub_date else ""

        header = f"## 🚀 {title}{date_str}"
        blocks.append(f"{header}\n\n{notes}")

    return "\n\n---\n\n".join(blocks)


def check_github_updates(
    repo: str = GITHUB_REPO,
    current_version: str = APP_VERSION,
    include_prereleases: bool = True,
    all_releases: Optional[list[Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """
    Odpytuje API GitHuba o najnowsze wydania repozytorium.
    Zwraca słownik z informacjami o aktualizacji lub None jeśli brak nowszej wersji.
    Wzbogaca wynik o pełną listę brakujących wersji ('newer_releases') oraz zsumowany changelog ('aggregated_notes').
    """
    if all_releases is None:
        all_releases = fetch_all_releases(repo=repo, include_prereleases=include_prereleases)
    if not all_releases:
        return None

    # Wszystkie wydania nowsze niż bieżąca wersja użytkownika
    newer_releases = [r for r in all_releases if is_newer_version(r["tag_name"], current_version)]
    if not newer_releases:
        return None

    latest = newer_releases[0]
    aggregated_notes = build_aggregated_changelog(newer_releases)

    return {
        "has_update": True,
        "current_version": current_version,
        "latest_version": latest["tag_name"],
        "release_title": latest["release_title"],
        "release_notes": latest["release_notes"],
        "published_at": latest["published_at"],
        "is_prerelease": latest["is_prerelease"],
        "html_url": latest["html_url"],
        "download_url": latest["download_url"],
        "asset_name": latest["asset_name"],
        "asset_size": latest["asset_size"],
        "newer_releases": newer_releases,
        "aggregated_notes": aggregated_notes,
        "all_releases": all_releases,
    }


class CheckUpdateWorker(QThread):
    """Asynchroniczny wątek sprawdzający dostępność aktualizacji na GitHubie."""
    update_checked_signal = pyqtSignal(object)  # (dict_or_None)
    error_signal = pyqtSignal(str)

    def __init__(self, include_prereleases: bool = True):
        super().__init__()
        self.include_prereleases = include_prereleases

    def run(self):
        try:
            all_releases = fetch_all_releases(
                include_prereleases=self.include_prereleases,
                raise_for_error=True
            )
            res = check_github_updates(
                include_prereleases=self.include_prereleases,
                all_releases=all_releases
            )
            if res is None:
                # Brak nowszej wersji, ale przekazujemy pobraną historię wydań dla okna ustawień
                res = {
                    "has_update": False,
                    "current_version": APP_VERSION,
                    "latest_version": all_releases[0]["tag_name"] if all_releases else APP_VERSION,
                    "newer_releases": [],
                    "aggregated_notes": "",
                    "all_releases": all_releases,
                }
            self.update_checked_signal.emit(res)
        except Exception as e:
            self.error_signal.emit(str(e))


class DownloadUpdateWorker(QThread):
    """Asynchroniczny wątek pobierający paczkę ZIP z aktualizacją z GitHuba."""
    progress_signal = pyqtSignal(int, str)  # (procent, status_text)
    download_finished_signal = pyqtSignal(bool, str, str)  # (sukces, zip_path_lub_error, wersja)

    def __init__(self, download_url: str, target_version: str):
        super().__init__()
        self.download_url = download_url
        self.target_version = target_version
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        if not self.download_url:
            self.download_finished_signal.emit(False, "Brak bezpośredniego adresu URL do pliku ZIP w wydaniu.", self.target_version)
            return

        temp_dir = tempfile.gettempdir()
        zip_path = os.path.join(temp_dir, f"InteligentnyDyktafonAI_{self.target_version}.zip")
        
        try:
            self.progress_signal.emit(5, "Nawiązywanie połączenia z serwerem wydań GitHub...")
            headers = {"User-Agent": f"Recorder67-App/{APP_VERSION}"}
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            req = urllib.request.Request(self.download_url, headers=headers)
            
            with urllib.request.urlopen(req, timeout=30) as response:
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                block_size = 1024 * 64  # 64 KB bufor
                
                with open(zip_path, "wb") as out_file:
                    while True:
                        if self._is_cancelled:
                            out_file.close()
                            if os.path.exists(zip_path):
                                os.remove(zip_path)
                            self.download_finished_signal.emit(False, "Pobieranie anulowane przez użytkownika.", self.target_version)
                            return
                            
                        buffer = response.read(block_size)
                        if not buffer:
                            break
                            
                        out_file.write(buffer)
                        downloaded += len(buffer)
                        
                        if total_size > 0:
                            pct = int((downloaded / total_size) * 100)
                            d_mb = downloaded / (1024 * 1024)
                            t_mb = total_size / (1024 * 1024)
                            self.progress_signal.emit(min(98, max(5, pct)), f"Pobieranie aktualizacji: {d_mb:.1f} MB / {t_mb:.1f} MB ({pct}%)...")
                        else:
                            d_mb = downloaded / (1024 * 1024)
                            self.progress_signal.emit(50, f"Pobrano {d_mb:.1f} MB...")

            self.progress_signal.emit(100, "Pobieranie zakończone pomyślnie!")
            self.download_finished_signal.emit(True, zip_path, self.target_version)
        except Exception as e:
            self.download_finished_signal.emit(False, f"Błąd pobierania pliku: {e}", self.target_version)


def generate_updater_scripts(
    zip_path: str,
    app_dir: str,
    exe_path: str,
    current_pid: int,
    restart_after: bool = True
) -> Tuple[str, str]:
    """
    Generuje skrypt PowerShell z natywnym graficznym paskiem postępu Windows Forms
    oraz komplementarny skrypt wsadowy .bat jako fallback.
    Zwraca (ścieżka_ps1, ścieżka_bat).
    """
    temp_dir = tempfile.gettempdir()
    temp_extract = os.path.join(temp_dir, "InteligentnyDyktafonAI_Update")
    updater_ps1 = os.path.join(temp_dir, "run_app_update.ps1")
    updater_bat = os.path.join(temp_dir, "run_app_update.bat")

    restart_val = "1" if restart_after else "0"
    safe_zip = zip_path.replace('"', '`"')
    safe_app_dir = app_dir.replace('"', '`"')
    safe_exe = exe_path.replace('"', '`"')
    safe_extract = temp_extract.replace('"', '`"')

    ps1_content = f'''# Skrypt automatycznej instalacji aktualizacji z oknem postępu WinForms
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = "Inteligentny Dyktafon AI - Instalacja Aktualizacji"
$form.Size = New-Object System.Drawing.Size(500, 220)
$form.StartPosition = "CenterScreen"
$form.BackColor = [System.Drawing.ColorTranslator]::FromHtml("#1a1a26")
$form.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#edf2f4")
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = "Trwa instalowanie nowej wersji dyktafonu..."
$lblTitle.Location = New-Object System.Drawing.Point(24, 18)
$lblTitle.Size = New-Object System.Drawing.Size(450, 26)
$lblTitle.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$lblTitle.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#4cc9f0")
$form.Controls.Add($lblTitle)

$lblStatus = New-Object System.Windows.Forms.Label
$lblStatus.Text = "Inicjalizacja procedury aktualizacji..."
$lblStatus.Location = New-Object System.Drawing.Point(24, 50)
$lblStatus.Size = New-Object System.Drawing.Size(450, 20)
$lblStatus.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$lblStatus.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#edf2f4")
$form.Controls.Add($lblStatus)

$lblDetail = New-Object System.Windows.Forms.Label
$lblDetail.Text = "Proszę czekać, pliki programu są bezpiecznie podmieniane..."
$lblDetail.Location = New-Object System.Drawing.Point(24, 72)
$lblDetail.Size = New-Object System.Drawing.Size(450, 18)
$lblDetail.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$lblDetail.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#8d99ae")
$form.Controls.Add($lblDetail)

$pb = New-Object System.Windows.Forms.ProgressBar
$pb.Location = New-Object System.Drawing.Point(24, 98)
$pb.Size = New-Object System.Drawing.Size(445, 24)
$pb.Minimum = 0
$pb.Maximum = 100
$pb.Value = 5
$form.Controls.Add($pb)

$form.Show()
$form.Refresh()
[System.Windows.Forms.Application]::DoEvents()

function Set-UpdateProgress($pct, $status, $detail) {{
    $pb.Value = [Math]::Min(100, [Math]::Max(0, $pct))
    $lblStatus.Text = $status
    if ($detail) {{ $lblDetail.Text = $detail }}
    $form.Refresh()
    [System.Windows.Forms.Application]::DoEvents()
}}

try {{
    # Krok 1: Oczekiwanie na zamknięcie procesu aplikacji
    Set-UpdateProgress 12 "Oczekiwanie na zamknięcie poprzedniej wersji programu..." "Zwalnianie plików procesu PID: {current_pid}"
    $timeout = 25
    $elapsed = 0
    while ((Get-Process -Id {current_pid} -ErrorAction SilentlyContinue) -and ($elapsed -lt $timeout)) {{
        Start-Sleep -Milliseconds 400
        $elapsed += 1
        [System.Windows.Forms.Application]::DoEvents()
    }}
    if (Get-Process -Id {current_pid} -ErrorAction SilentlyContinue) {{
        Stop-Process -Id {current_pid} -Force -ErrorAction SilentlyContinue
    }}
    Start-Sleep -Milliseconds 600

    # Krok 2: Przygotowanie katalogu roboczego
    Set-UpdateProgress 25 "Przygotowywanie plików tymczasowych..." "Czyszczenie poprzednich danych aktualizacji..."
    if (Test-Path "{safe_extract}") {{
        Remove-Item -Path "{safe_extract}" -Recurse -Force -ErrorAction SilentlyContinue
    }}
    New-Item -ItemType Directory -Path "{safe_extract}" -Force | Out-Null

    # Krok 3: Rozpakowanie archiwum ZIP
    Set-UpdateProgress 45 "Rozpakowywanie nowej wersji dyktafonu..." "Wyodrębnianie plików aktualizacji..."
    tar -xf "{safe_zip}" -C "{safe_extract}" 2>$null
    if (-not (Test-Path "{safe_extract}\\*")) {{
        Expand-Archive -Path "{safe_zip}" -DestinationPath "{safe_extract}" -Force
    }}

    # Krok 4: Podmiana plików w folderze aplikacji
    Set-UpdateProgress 75 "Instalowanie nowych plików programu..." "Kopiowanie do katalogu: {safe_app_dir}"
    $sourceDir = "{safe_extract}"
    $exeFound = Get-ChildItem -Path "{safe_extract}" -Filter "InteligentnyDyktafonAI.exe" -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($exeFound) {{
        $sourceDir = $exeFound.DirectoryName
    }} elseif (Test-Path "{safe_extract}\\InteligentnyDyktafonAI") {{
        $sourceDir = "{safe_extract}\\InteligentnyDyktafonAI"
    }}
    & robocopy "$sourceDir" "{safe_app_dir}" /e /np /r:5 /w:1 | Out-Null
    if ($LASTEXITCODE -ge 8) {{
        throw "Błąd kopiowania plików aplikacji (robocopy exit code: $LASTEXITCODE). Upewnij się, że masz uprawnienia do zapisu w katalogu: {safe_app_dir}"
    }}

    # Krok 5: Porządkowanie
    Set-UpdateProgress 95 "Finalizowanie instalacji..." "Usuwanie pobranego archiwum i plików roboczych..."
    if (Test-Path "{safe_zip}") {{
        Remove-Item -Path "{safe_zip}" -Force -ErrorAction SilentlyContinue
    }}
    if (Test-Path "{safe_extract}") {{
        Remove-Item -Path "{safe_extract}" -Recurse -Force -ErrorAction SilentlyContinue
    }}

    # Krok 6: Gotowe
    Set-UpdateProgress 100 "Aktualizacja zainstalowana pomyślnie!" "Wszystkie pliki zostały zaktualizowane."
    Start-Sleep -Milliseconds 600

    if ("{restart_val}" -eq "1") {{
        $lblStatus.Text = "Uruchamianie nowej wersji dyktafonu..."
        $form.Refresh()
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 500
        Start-Process -FilePath "{safe_exe}"
    }}
    $form.Close()
    exit 0
}}
catch {{
    $lblTitle.Text = "Błąd podczas instalacji aktualizacji"
    $lblTitle.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#ef4444")
    $lblStatus.Text = $_.Exception.Message
    $lblDetail.Text = "Sprawdź uprawnienia do folderu programu."
    $form.Refresh()
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Seconds 6
    $form.Close()
    exit 1
}}
'''

    restart_cmd_bat = f'start "" "{exe_path}"' if restart_after else 'rem Brak restartu (aktualizacja przy wyjsciu)'
    bat_content = f'''@echo off
rem Skrypt nadrzędny podmiany plików Recorder67 z automatycznym fallbackiem
echo [UPDATER] Uruchamianie graficznego instalatora PowerShell...
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{updater_ps1}"
if not errorlevel 1 goto cleanup_exit

rem Jesli PowerShell byl zablokowany lub zakonczyl sie bledem, fallback do klasycznego cmd:
echo [UPDATER] Uruchamianie procedury awaryjnej (batch fallback)...
:wait_process
ping 127.0.0.1 -n 2 > nul
tasklist /fi "pid eq {current_pid}" 2>nul | find "{current_pid}" >nul
if not errorlevel 1 goto wait_process

if exist "{temp_extract}" rmdir /s /q "{temp_extract}" 2>nul
mkdir "{temp_extract}"
tar -xf "{zip_path}" -C "{temp_extract}" 2>nul
if not exist "{temp_extract}\\*" powershell -NoProfile -Command "Expand-Archive -Path '{zip_path}' -DestinationPath '{temp_extract}' -Force"

set "SRC={temp_extract}"
if exist "{temp_extract}\\InteligentnyDyktafonAI\\InteligentnyDyktafonAI.exe" set "SRC={temp_extract}\\InteligentnyDyktafonAI"
for /d %%D in ("{temp_extract}\\*") do if exist "%%D\\InteligentnyDyktafonAI.exe" set "SRC=%%D"

robocopy "%SRC%" "{app_dir}" /e /np /r:5 /w:1 > nul

if exist "{zip_path}" del /f /q "{zip_path}" 2>nul
if exist "{temp_extract}" rmdir /s /q "{temp_extract}" 2>nul
{restart_cmd_bat}

:cleanup_exit
exit /b 0
'''

    with open(updater_ps1, "w", encoding="utf-8") as f:
        f.write(ps1_content)

    with open(updater_bat, "w", encoding="cp852", errors="replace") as f:
        f.write(bat_content)

    return (updater_ps1, updater_bat)


def apply_in_place_update(zip_path: str, restart_after: bool = True) -> bool:
    """
    Przygotowuje i uruchamia skrypt podmiany plików w tle, po czym zamyka bieżącą aplikację.
    Wyświetla natywny graficzny pasek postępu Windows Forms z informacją o poszczególnych etapach instalacji.
    Parametr restart_after określa, czy po podmianie plików aplikacja ma się samoczynnie zrestartować.
    """
    if not os.path.exists(zip_path):
        return False

    is_frozen = getattr(sys, "frozen", False)
    if not is_frozen:
        # Tryb deweloperski (uruchomiony ze skryptu .py)
        # Nie nadpisujemy środowiska deweloperskiego plikami skompilowanymi .exe
        return False

    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    exe_path = sys.executable
    current_pid = os.getpid()

    try:
        ps1_path, bat_path = generate_updater_scripts(
            zip_path=zip_path,
            app_dir=app_dir,
            exe_path=exe_path,
            current_pid=current_pid,
            restart_after=restart_after
        )

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        else:
            creationflags |= 0x08000000

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

        # Uruchomienie skryptu wsadowego z ukrytą konsolą (okno WinForms wyświetli się na pulpicie)
        # Dzięki temu skrypt PowerShell wyświetla nowoczesny pasek postępu, a w razie blokady PowerShell
        # natychmiast zadziała awaryjny fallback bez przerywania aktualizacji.
        subprocess.Popen(
            [
                "cmd.exe",
                "/c",
                bat_path
            ],
            creationflags=creationflags,
            startupinfo=startupinfo,
            close_fds=True
        )
        return True
    except Exception as e:
        print(f"[UPDATER] Błąd uruchomienia skryptu aktualizatora: {e}")
        return False
