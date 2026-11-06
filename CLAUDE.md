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
  Elemente vor `st.tabs` müssen bei jedem Lauf gleich sein (Ladebalken nur im festen Platzhalter `load_box`),
  und die Tabs haben `key="main_tabs"` – sonst springt die App nach einem Widget-Klick auf den ersten Tab.
- `turnaround/data.py` – yfinance-Kurse (Bulk-Download) und Fundamentaldaten (Threadpool), Pickle-Cache
  in `cache/` (Kurse 12 h, Fundamental 24 h). `derive_fundamentals` leitet Margentrend, Gewinnwende,
  FCF, Schuldenänderung ab.
- `turnaround/news.py` – Yahoo-News (mit Relevanzfilter) + Google-News-RSS (DE/EN, Zeitfilter
  `after:`/`before:` für historische Wendepunkte). `LEXICON` = (Muster, Gewicht, Kategorie);
  Muster ≤ 6 Zeichen nur am Wortanfang. Kategorie `Existenz` = Red Flag. Cache 6 h.
- `turnaround/analysis.py` – `detect_turnaround` (vektorisiert: Einbruch ggü. laufendem Max, Erholung
  ggü. künftigem Max), `price_signals`, sechs Teil-Scores 0–1 (`FACTOR_HELP`), `risk_flags` mit
  multiplikativen Abschlägen (`FLAG_PENALTY`), `analyze_universe`, `signal_backtest` und `score_as_of` (nur Kurssignale, Stichtag-genau –
  Grundlage für „Auswahl zum Startdatum“ im Musterportfolio-Rückblick).
- `turnaround/portfolio.py` – Auswahl mit Sektor-Limit, Gewichtung + `cap_weights`, Rückblick-Zeitraum
  (`period_start`, `period_returns`, `backward_performance`), Snapshots als JSON
  in `portfolios/`.
- `turnaround/charts.py` – Plotly. Feste Farbrollen S1/S2/S3/RING je Modus in `PALETTES`
  (`set_mode("light"|"dark")` vor dem Zeichnen), MUTED; Statusfarben (GOOD/CRITICAL) nur für
  positiv/negativ. Keine Doppelachsen, Farben nie zyklisch. Marker-Ringe in `RING` (= Hintergrund), nie „white“.
- `turnaround/universe.py` – Tickerlisten (Yahoo-Format); ungültige Ticker werden still übersprungen.
- `turnaround/brokers/` – Broker-Clients, **nur lesend** (siehe unten).

## Broker-Anbindung (Bitpanda, eToro)

- Schlüssel stehen in `.env` (Vorlage: `.env.example`): `FUSION_API_KEY`, optional `BITPANDA_API_KEY`,
  `ETORO_API_KEY`, `ETORO_USER_KEY`, optional `ETORO_API_WRITE`. **Werte nie ausgeben, loggen oder committen**; zum Prüfen nur die
  Namen lesen (`sed -E 's/=.*//' .env`). `BrokerError`-Meldungen enthalten bewusst keine Header.
- `base.py`: `HttpClient` (Timeout, 429 → `Retry-After`), Datenklassen `Holding`/`AccountSummary`.
- `bitpanda.py`: `FusionClient` (`https://api.fusion.bitpanda.com`, Header `x-api-key`; `/v1/account`,
  `/v1/account/balances`, `/v1/tickers`, `/v1/account/trades|orders`). Die Struktur der Balances-Einträge ist
  nicht dokumentiert → defensiv geparst. `BitpandaClassicClient` (`https://api.public.bitpanda.com`,
  `/v1/portfolio`, `/v1/assets`) braucht einen eigenen Schlüssel – der Fusion-Schlüssel liefert dort 401.
- `etoro.py`: `EToroClient` (`https://public-api.etoro.com`, Header `x-api-key`, `x-user-key`, je Anfrage
  neue `x-request-id`). Positionen + GuV aus `/api/v1/trading/info/{real|demo}/pnl`, Namen über
  `/api/v2/market-data/instruments?instrumentsIds=` (sic), Kurse über `/api/v2/market-data/rates?instrumentIds=`.
  Limits: 60 Portfolio- bzw. 120 Marktdaten-Abfragen/Min. eToro-Symbole ≈ Yahoo-Ticker (Krypto → `-USD`).
- Die UI (Tab „Depots“) cacht `brokers.fetch_all` 5 Minuten.
- **Orders nur im eToro-Demokonto** (Nutzerwunsch): `EToroClient.open_demo_order` / `close_demo_position`
  prüfen `_require_demo()`; Echtgeld-Orders gibt es bewusst nicht – nicht ohne ausdrücklichen Auftrag ändern.
  Kauf: `POST /api/v2/trading/execution/demo/orders` (camelCase, `amount` in USD, `leverage` 1),
  Schließen: `POST /api/v1/trading/execution/demo/market-close-orders/positions/{id}` (PascalCase
  `InstrumentID`, `UnitsToDeduct` null = ganz). Asynchron → Status über `demo/orders:lookup?orderId=` bzw.
  `demo/close-orders/{orderId}`; Status 11 = wartet auf Marktöffnung. Max. 20 Order-Anfragen/Min. (`pause`).
- Schreibrecht: eToro-Schlüssel gelten je Umgebung (Real/Demo) und Berechtigung (Read/Write). Optional
  `ETORO_API_WRITE` ist ein **User-Key** (Demo, Write) und wird nur für Order-POSTs als `x-user-key` gesendet
  (`_post_order`); er darf **keine Marktdaten** lesen (403), daher bleibt `ETORO_USER_KEY` für alle GETs.
  (Alternativ weiter möglich: `ETORO_DEMO_API_KEY`/`ETORO_DEMO_USER_KEY`.) 403 → `WRITE_PERMISSION_HINT`,
  `execute_plan` bricht dann ab. Stand 06.11.2026: Test-Order 50 USD BTC im Demokonto gekauft und geschlossen – OK.
- `rebalance.py`: `plan_rebalance` (rein, testbar) → `build_plan` (liest Demokonto) → `execute_plan`
  (erst Verkäufe, Guthaben neu lesen, dann Käufe). Protokoll in `portfolios/demo_sync_log.jsonl`.
  UI: Schalter `demo_sync` → Vorschau → Bestätigungs-Checkbox → Ausführen → Depot-Cache leeren, Schalter zurück.
- Live-Test-Orders nur nach Rückfrage beim Nutzer, und nur im Demokonto.
- Tests in `tests/test_brokers.py` laufen ohne Netz (simulierte Antworten über `monkeypatch` auf `get`).

## Darkmode

Farbschemata in `.streamlit/config.toml` (`[theme.light]`, `[theme.dark]`). Streamlit hat keine API zum
Umschalten zur Laufzeit: Der Sidebar-Umschalter schreibt per `st.html(..., unsafe_allow_javascript=True)`
den Frontend-Schlüssel `stActiveTheme-<pfad>-v2` (`"Light"|"Dark"|"System"`) in den localStorage und lädt
die Seite mit `?theme=…` neu. Der URL-Parameter hält die Wahl über das Neuladen und steuert `charts.set_mode`;
ein zweites Skript gleicht beim Laden URL und localStorage ab (Lesezeichen, neuer Browser).
Fallstricke: Skripte in eigenen, benannten Containern rendern (sonst werden sie nicht erneut ausgeführt) und
in `(() => {…})()` kapseln (doppelte globale `const` bricht das zweite Skript ab).
Bei einem Streamlit-Update prüfen, ob der Schlüssel noch stimmt (`grep stActiveTheme .venv/Lib/site-packages/streamlit/static`).

## Konventionen

- UI-Texte, Spaltennamen und Kommentare auf Deutsch. Spaltennamen in `analyze_universe` sind die
  Schnittstelle zur UI – beim Umbenennen `app.py` mitziehen.
- Prozentwerte intern als Anteil (0,25); erst für die Anzeige mit `pct()` × 100.
- Fehlende Daten als `NaN` und in Mittelwerten auslassen (`_nanmean`), nicht als 0 werten.
- `width="stretch"` statt des veralteten `use_container_width`.
- Disclaimer „keine Anlageberatung“ und die Hinweise zu Rückschaufehler/Survivorship-Bias beibehalten.
