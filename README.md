# Turnaround-Scanner

Streamlit-App, die **Turnaround-Aktien der letzten Jahre** erkennt, Kurs- und Fundamentaldaten mit
**Unternehmens- und Börsennachrichten** abgleicht und ein **Musterportfolio möglicher künftiger
Turnaround-Kandidaten** zusammenstellt.

> Analyse- und Lernwerkzeug – **keine Anlageberatung**.

## Start

Doppelklick auf `start.bat` – oder manuell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app.py
```

Die Oberfläche öffnet sich unter http://localhost:8501. Der erste Lauf lädt die Daten (~1 Min. für
DAX + MDAX/SDAX), danach kommt alles aus dem Cache in `cache/`.

## Funktionen

| Tab | Inhalt |
|---|---|
| Übersicht | Kennzahlen, alle Titel nach Abschlag vs. Score, Phasen-Verteilung, Top 10 |
| Historische Turnarounds | Einbruch ≥ X % und Erholung ≥ Y %; Fallstudie mit Wendepunkten im Chart und historischen News rund um den Tiefpunkt |
| Kandidaten | Sechs-Faktoren-Score (Abschlag, Bodenbildung, Momentum, Fundamental, Bewertung, News), Risiko-Flags, CSV-Export |
| News-Abgleich | Yahoo Finance + Google News (DE/EN), Turnaround-Lexikon, Abgleich Signale ↔ Nachrichtenlage |
| Musterportfolio | Auswahl mit Sektor-Limit und Gewichtsobergrenze, Begründung je Position, Stückzahlen, Snapshots zum Nachverfolgen |
| Einzelanalyse | Kurs mit 50/200-Tage-Linie, Score-Zusammensetzung, Umsatz/Ergebnis/Marge, Profil, News |
| Signal-Check | Rollierender Test der Kurssignale an vergangenen Stichtagen |
| Methodik | Erklärung aller Regeln |

Alle Schwellen und Gewichte sind in der Seitenleiste einstellbar. Eigene Ticker im Yahoo-Format
(z. B. `LHA.DE`, `RR.L`, `INTC`) können ergänzt werden; Universen stehen in `turnaround/universe.py`.

## Aufbau

```
app.py                  Streamlit-Oberfläche
turnaround/universe.py  Aktienlisten
turnaround/data.py      Kurse & Fundamentaldaten (yfinance) + Cache
turnaround/news.py      Nachrichten & Stimmungs-Lexikon
turnaround/analysis.py  Turnaround-Erkennung, Scoring, Signal-Check
turnaround/portfolio.py Portfolio-Konstruktion & Snapshots
turnaround/charts.py    Plotly-Charts
tests/                  pytest
```

Tests: `.venv\Scripts\python -m pytest tests`

## Grenzen

- Kostenlose Datenquellen ohne Gewähr; Fundamentaldaten meist nur 4 Jahre / 5–6 Quartale.
- News-Stimmung per Stichwort-Lexikon – erkennt keine Ironie oder komplexen Zusammenhänge.
- Signal-Check nur mit Kursdaten (Fundamentaldaten/News sind nicht historisch verfügbar) und mit
  Survivorship-Bias (nur heute gelistete Titel).
