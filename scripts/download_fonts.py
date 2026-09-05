"""
Pobiera i weryfikuje czcionkę Saira (SIL OFL 1.1) do katalogu recorder/resources/fonts/.
Uruchomienie:
    python scripts/download_fonts.py
"""

import os
import sys
import hashlib
import urllib.request

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(ROOT_DIR, "recorder", "resources", "fonts")

ASSETS = {
    "OFL.txt": {
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/saira/OFL.txt",
        "expected_sha256": "f2665d4718b452b3818a877191355ac884a6b9b419d35408fe7ee487e9e8f30f",
        "min_size": 4000,
    },
    "Saira[wdth,wght].ttf": {
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/saira/Saira%5Bwdth%2Cwght%5D.ttf",
        "expected_sha256": "9d050fc5a01c85f74c4257c207d50b55d1e40c37308c642f974a2c5003231dde",
        "min_size": 450000,
    },
}


def download_and_verify() -> bool:
    os.makedirs(FONTS_DIR, exist_ok=True)
    all_ok = True

    for filename, meta in ASSETS.items():
        dest_path = os.path.join(FONTS_DIR, filename)

        # Check existing file
        if os.path.exists(dest_path):
            with open(dest_path, "rb") as f:
                content = f.read()
            sha = hashlib.sha256(content).hexdigest()
            if sha == meta["expected_sha256"]:
                print(f"✅ {filename} jest już obecny i zweryfikowany (SHA256 OK).")
                continue

        print(f"📥 Pobieranie {filename} z {meta['url']}...")
        try:
            req = urllib.request.Request(
                meta["url"],
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Recorder67/FontDownloader"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()

            if len(data) < meta["min_size"]:
                print(f"❌ Rozmiar pliku {filename} zbyt mały ({len(data)} B)!")
                all_ok = False
                continue

            calc_sha = hashlib.sha256(data).hexdigest()
            if calc_sha != meta["expected_sha256"]:
                print(f"⚠️ Ostrzeżenie: SHA256 dla {filename} różni się od oczekiwanego ({calc_sha})! Zapisuję...")

            with open(dest_path, "wb") as f:
                f.write(data)

            print(f"✅ Pomyślnie pobrano i zapisano {filename} ({len(data)} bajtów) do {dest_path}")
        except Exception as e:
            print(f"❌ Błąd podczas pobierania {filename}: {e}")
            all_ok = False

    return all_ok


if __name__ == "__main__":
    success = download_and_verify()
    sys.exit(0 if success else 1)
