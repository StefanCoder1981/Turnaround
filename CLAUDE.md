# CLAUDE.md

Turnaround-Scanner: Streamlit-App (deutsche Oberfläche), die historische Turnaround-Aktien erkennt,
Kurs-/Fundamentaldaten mit Unternehmens- und Börsennachrichten abgleicht und ein Musterportfolio
möglicher künftiger Turnaround-Kandidaten vorschlägt.

## Git-Workflow

- **Implementierungen dürfen immer ohne Rückfrage committet und gepusht werden** (`origin` → GitHub,
  Branch `main`). Vor dem Commit Tests laufen lassen; bei roten Tests nicht pushen.
- Niemals `.env`, `cache/`, `portfolios/` oder `.venv/` committen (stehen in `.gitignore`).

## Befehle (Windows, venv in `.venv`)

```powershell
.venv\Scripts\python -m pip install -r requirements.txt   # Abhängigkeiten
.venv\Scripts\python -m streamlit run app.py               # App starten (oder start.bat)
.venv\Scripts\python -m pytest tests                       # Unit-Tests
```

GUI-Test ohne Browser über `streamlit.testing.v1.AppTest.from_file("app.py", default_timeout=900)`;
lädt echte Daten (Internet nötig, erster Lauf ~1 Min.). Selectboxen per Widget-`key` ansprechen
(`single_pick`, `hist_pick`, `news_pick`) und mit dem Ticker, nicht dem Anzeigetext, setzen.

## Architektur

- `app.py` – gesamte UI: Seitenleiste (Parameter) → Daten laden (in `st.session_state`, Schlüssel =
  Ticker-Tupel) → vorläufiges Scoring → News nur für Top-N-Kandidaten nachladen → finales Scoring → 8 Tabs.
  Scoring läuft bei jedem Rerun neu (billig); nur Downloads sind gecacht.
- `turnaround/data.py` – yfinance-Kurse (Bulk-Download) und Fundamentaldaten (Threadpool), Pickle-Cache
  in `cache/` (Kurse 12 h, Fundamental 24 h). `derive_fundamentals` leitet Margentrend, Gewinnwende,
  FCF, Schuldenänderung ab.
- `turnaround/news.py` – Yahoo-News (mit Relevanzfilter) + Google-News-RSS (DE/EN, Zeitfilter
  `after:`/`before:` für historische Wendepunkte). `LEXICON` = (Muster, Gewicht, Kategorie);
  Muster ≤ 6 Zeichen nur am Wortanfang. Kategorie `Existenz` = Red Flag. Cache 6 h.
- `turnaround/analysis.py` – `detect_turnaround` (vektorisiert: Einbruch ggü. laufendem Max, Erholung
  ggü. künftigem Max), `price_signals`, sechs Teil-Scores 0–1 (`FACTOR_HELP`), `risk_flags` mit
  multiplikativen Abschlägen (`FLAG_PENALTY`), `analyze_universe`, `signal_backtest` (nur Kurssignale).
- `turnaround/portfolio.py` – Auswahl mit Sektor-Limit, Gewichtung + `cap_weights`, Snapshots als JSON
  in `portfolios/`.
- `turnaround/charts.py` – Plotly. Feste Farbrollen S1/S2/S3/RING je Modus in `PALETTES`
  (`set_mode("light"|"dark")` vor dem Zeichnen), MUTED; Statusfarben (GOOD/CRITICAL) nur für
  positiv/negativ. Keine Doppelachsen, Farben nie zyklisch. Marker-Ringe in `RING` (= Hintergrund), nie „white“.

## Darkmode

Farbschemata in `.streamlit/config.toml` (`[theme.light]`, `[theme.dark]`). Streamlit hat keine API zum
Umschalten zur Laufzeit: Der Sidebar-Umschalter schreibt per `st.html(..., unsafe_allow_javascript=True)`
den Frontend-Schlüssel `stActiveTheme-<pfad>-v2` (`"Light"|"Dark"|"System"`) in den localStorage und lädt
die Seite mit `?theme=…` neu. Der URL-Parameter hält die Wahl über das Neuladen und steuert `charts.set_mode`.
Bei einem Streamlit-Update prüfen, ob der Schlüssel noch stimmt (`grep stActiveTheme .venv/Lib/site-packages/streamlit/static`).
- `turnaround/universe.py` – Tickerlisten (Yahoo-Format); ungültige Ticker werden still übersprungen.

## Konventionen

- UI-Texte, Spaltennamen und Kommentare auf Deutsch. Spaltennamen in `analyze_universe` sind die
  Schnittstelle zur UI – beim Umbenennen `app.py` mitziehen.
- Prozentwerte intern als Anteil (0,25); erst für die Anzeige mit `pct()` × 100.
- Fehlende Daten als `NaN` und in Mittelwerten auslassen (`_nanmean`), nicht als 0 werten.
- `width="stretch"` statt des veralteten `use_container_width`.
- Disclaimer „keine Anlageberatung“ und die Hinweise zu Rückschaufehler/Survivorship-Bias beibehalten.
