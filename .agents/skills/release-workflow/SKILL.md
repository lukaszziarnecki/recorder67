---
name: release-workflow
description: >-
  Kompletna procedura przygotowania, weryfikacji i publikacji nowego wydania aplikacji recorder67.
  Używaj tego skilla za każdym razem, gdy użytkownik planuje nowe wydanie (zarówno duże ze scalaniem
  wielu gałęzi, jak i małe z pojedynczego brancha) oraz do redagowania opisu Release Notes na GitHubie.
---

# Procedura Wydania (Release Workflow) - recorder67

## Krok 1: Przygotowanie gałęzi
* Upewnij się, że gałąź bazowa `master` jest zsynchronizowana z `origin/master`.
* Przygotuj zmiany do wydania:
  * **Wydanie wielogałęziowe:** Utwórz branch `release/vX.Y.Z`, scal gałęzie składowe, rozwiąż konflikty i wyrównaj pliki projektu Visual Studio (`recorder.slnx`, `recorder.pyproj`).
  * **Wydanie z pojedynczej gałęzi:** Sprawdź spójność plików i przygotuj merge do `master`.

## Krok 2: Weryfikacja jakościowa
* Uruchom pakiet testów: `.\env\Scripts\pytest -q` (wszystkie testy muszą przejść).
* Sprawdź kompilację: `python -m compileall recorder main.py run.py tests`.
* Upewnij się, że w kodzie i testach nie ma prawdziwych sekretów z `.env`.

## Krok 3: Ocena dokumentacji (Przed tagiem!)
* Oceń wprowadzone zmiany pod kątem użytkownika końcowego.
* Jeśli doszły nowe opcje w Ustawieniach, nowe moduły lub widoczne usprawnienia, zaktualizuj `README.md` oraz `PROJECT_GOAL.md`.

## Krok 4: Wersjonowanie (SemVer)
* Wybierz numer wersji:
  * `0.X.0` – duże kamienie milowe, nowe okna, duże optymalizacje silnika.
  * `0.X.Y` – drobniejsze poprawki, łatki stabilności.
* Zaktualizuj `APP_VERSION = "X.Y.Z"` w `recorder/config.py` i zatwierdź commit.
* Scal gałąź wydania do `master`.

## Krok 5: Tagowanie i publikacja
* Utwórz tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`.
* Wypchnij: `git push origin master; git push origin vX.Y.Z`.

## Krok 6: Monitorowanie GitHub Actions
* Workflow `.github/workflows/release.yml` buduje paczkę `.zip` (ok. 10–13 min).
* Monitoruj status przez GitHub API do momentu ukończenia kroku `Publish GitHub Release`.

## Krok 7: Redagowanie Tytułu i Release Notes dla Użytkowników Końcowych
Zaktualizuj tytuł (`name`) oraz opis (`body`) wydania przez GitHub API (PATCH `/releases/:id`). Stosuj następujące zasady redakcyjne:

### Format Tytułu Wydania:
GitHub Actions domyślnie ustawia roboczy tytuł `Release vX.Y.Z`. **Zawsze zaktualizuj go** do formatu:
`vX.Y.Z: [Zwięzła, zrozumiała dla użytkownika nazwa głównych zmian / poprawek]`
*Przykłady:*
- Wydanie główne: `v0.7.0: Wygląd i Personalizacja`
- Łatka stabilności / hotfix: `v0.6.1: Stabilizacja synchronizacji na żywo z CRM i odporność na zakłócenia sieci`
- Łatka komponentu: `v0.7.1: Samonaprawiający się watchdog mikrofonu, deduplikacja urządzeń i priorytet WASAPI`

### Zasady Redakcyjne Opisu (Dla Użytkowników Aplikacji):
1. **Czysta delta:** Opisuj wyłącznie realną różnicę między poprzednim wydaniem a obecnym. Pomijaj próby robocze, eksperymenty z czatu czy błędy popełnione i naprawione w trakcie pracy nad danym wydaniem.
2. **Język korzyści i funkcji:** Pisz przystępnym językiem dla użytkownika programu, unikając nazw zmiennych, technicznych funkcji czy fragmentów kodu (chyba że chodzi o konfigurację `.env`).
3. **Umiar w emotkach:** Stosuj czysty, profesjonalny styl bez nadmiaru ikon.

### Sprawdzony Szablon Opisu:
```markdown
Wersja **vX.Y.Z** wprowadza [zwięzłe podsumowanie głównego celu wydania w 1-2 zdaniach].

### Główne nowości
* **Nazwa funkcji:** Zrozumiały dla człowieka opis co funkcja robi i jak ułatwia pracę.

### Wydajność i stabilność
* **Opis usprawnienia:** Co działa szybciej, płynniej lub nie obciąża komputera (np. stabilność przy wielogodzinnych nagraniach).

### Poprawki błędów i usprawnienia
* **Opis naprawionego zachowania:** Co wcześniej nie działało lub zachowywało się niepoprawnie, a teraz działa prawidłowo.

**Pełna lista zmian:** <https://github.com/lukaszziarnecki/recorder67/compare/vPoprzednia...vX.Y.Z>
```
