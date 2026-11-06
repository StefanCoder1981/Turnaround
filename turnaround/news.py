"""Unternehmens- und Börsennachrichten + turnaround-spezifische Stimmungsanalyse.

Quellen: Yahoo-Finance-News (meist englisch) und Google-News-RSS (deutsch).
Die Stimmung wird mit einem auf Turnarounds zugeschnittenen DE/EN-Lexikon
bewertet; jüngere Meldungen zählen stärker (Halbwertszeit 21 Tage).
"""
from __future__ import annotations

import html
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser
import numpy as np
import pandas as pd
import yfinance as yf

from .data import ProgressFn, cache_get, cache_set

NEWS_TTL_H = 6
HALF_LIFE_DAYS = 21

# (Muster, Gewicht, Kategorie). Muster werden als Teilstring im Kleinbuchstaben-Text gesucht.
LEXICON: list[tuple[str, float, str]] = [
    # --- Turnaround-Signale (positiv) ---
    ("turnaround", 1.0, "Turnaround"), ("trendwende", 1.0, "Turnaround"), ("comeback", 0.8, "Turnaround"),
    ("restructuring", 0.4, "Restrukturierung"), ("restrukturierung", 0.4, "Restrukturierung"),
    ("sanierung", 0.3, "Restrukturierung"), ("neuausrichtung", 0.5, "Restrukturierung"),
    ("transformation", 0.3, "Restrukturierung"), ("spin-off", 0.4, "Restrukturierung"),
    ("abspaltung", 0.4, "Restrukturierung"), ("cost cut", 0.5, "Restrukturierung"),
    ("sparprogramm", 0.5, "Restrukturierung"), ("kostensenkung", 0.5, "Restrukturierung"),
    ("efficiency program", 0.5, "Restrukturierung"), ("effizienzprogramm", 0.5, "Restrukturierung"),
    ("new ceo", 0.6, "Management"), ("neuer ceo", 0.6, "Management"), ("neuer chef", 0.6, "Management"),
    ("neuer vorstandschef", 0.6, "Management"), ("appoints", 0.3, "Management"), ("activist", 0.5, "Management"),
    ("aktivist", 0.5, "Management"),
    ("raises guidance", 1.0, "Prognose"), ("raised guidance", 1.0, "Prognose"), ("raises outlook", 1.0, "Prognose"),
    ("hebt prognose", 1.0, "Prognose"), ("prognose angehoben", 1.0, "Prognose"), ("erhöht prognose", 1.0, "Prognose"),
    ("hebt ausblick", 1.0, "Prognose"), ("bestätigt prognose", 0.4, "Prognose"), ("reaffirms", 0.4, "Prognose"),
    ("beats", 0.7, "Zahlen"), ("tops estimates", 0.8, "Zahlen"), ("übertrifft", 0.7, "Zahlen"),
    ("besser als erwartet", 0.8, "Zahlen"), ("better than expected", 0.8, "Zahlen"),
    ("return to profit", 1.0, "Zahlen"), ("back to profit", 1.0, "Zahlen"), ("schwarze zahlen", 1.0, "Zahlen"),
    ("gewinnzone", 0.8, "Zahlen"), ("profitable", 0.4, "Zahlen"), ("record", 0.4, "Zahlen"), ("rekord", 0.4, "Zahlen"),
    ("upgrade", 0.8, "Analysten"), ("hochgestuft", 0.8, "Analysten"), ("kaufempfehlung", 0.6, "Analysten"),
    ("raises price target", 0.6, "Analysten"), ("kursziel angehoben", 0.6, "Analysten"),
    ("hebt kursziel", 0.6, "Analysten"), ("outperform", 0.4, "Analysten"), ("overweight", 0.4, "Analysten"),
    ("buyback", 0.6, "Kapital"), ("aktienrückkauf", 0.6, "Kapital"), ("debt reduction", 0.6, "Kapital"),
    ("schuldenabbau", 0.6, "Kapital"), ("refinanc", 0.3, "Kapital"), ("insider buy", 0.8, "Insider"),
    ("insiderkauf", 0.8, "Insider"), ("directors' dealings", 0.3, "Insider"), ("kauft aktien", 0.4, "Insider"),
    ("rally", 0.4, "Markt"), ("surge", 0.5, "Markt"), ("soars", 0.5, "Markt"), ("jumps", 0.4, "Markt"),
    ("springt", 0.4, "Markt"), ("steigt", 0.2, "Markt"), ("erholung", 0.5, "Markt"), ("recovery", 0.5, "Markt"),
    ("rebound", 0.5, "Markt"), ("auftrag", 0.3, "Geschäft"), ("order", 0.1, "Geschäft"), ("deal", 0.2, "Geschäft"),
    ("übernahmeangebot", 0.6, "Geschäft"), ("takeover bid", 0.6, "Geschäft"),
    ("kaufsignal", 0.5, "Markt"), ("dreht auf", 0.4, "Markt"), ("kurssprung", 0.5, "Markt"),
    ("aufwärtstrend", 0.4, "Markt"), ("jahreshoch", 0.3, "Markt"), ("boden gefunden", 0.6, "Turnaround"),
    ("bodenbildung", 0.5, "Turnaround"), ("wende", 0.3, "Turnaround"), ("boom", 0.3, "Geschäft"),
    # --- Warnsignale (negativ) ---
    ("profit warning", -1.2, "Prognose"), ("gewinnwarnung", -1.2, "Prognose"), ("senkt prognose", -1.0, "Prognose"),
    ("prognose gesenkt", -1.0, "Prognose"), ("cuts guidance", -1.0, "Prognose"), ("lowers guidance", -1.0, "Prognose"),
    ("cuts outlook", -1.0, "Prognose"), ("kappt prognose", -1.0, "Prognose"), ("senkt ausblick", -1.0, "Prognose"),
    ("misses", -0.7, "Zahlen"), ("verfehlt", -0.7, "Zahlen"), ("schlechter als erwartet", -0.8, "Zahlen"),
    ("worse than expected", -0.8, "Zahlen"), ("verlust", -0.4, "Zahlen"), ("loss", -0.3, "Zahlen"),
    ("impairment", -0.6, "Zahlen"), ("abschreibung", -0.5, "Zahlen"), ("write-down", -0.6, "Zahlen"),
    ("downgrade", -0.8, "Analysten"), ("abgestuft", -0.8, "Analysten"), ("verkaufsempfehlung", -0.6, "Analysten"),
    ("cuts price target", -0.5, "Analysten"), ("kursziel gesenkt", -0.5, "Analysten"),
    ("senkt kursziel", -0.5, "Analysten"), ("underweight", -0.4, "Analysten"), ("underperform", -0.4, "Analysten"),
    ("kapitalerhöhung", -0.6, "Kapital"), ("capital increase", -0.6, "Kapital"), ("dilution", -0.6, "Kapital"),
    ("rights issue", -0.6, "Kapital"), ("dividend cut", -0.7, "Kapital"), ("dividendenkürzung", -0.7, "Kapital"),
    ("streicht dividende", -0.8, "Kapital"), ("rating cut", -0.6, "Kapital"), ("junk", -0.6, "Kapital"),
    ("insolvenz", -2.0, "Existenz"), ("insolvency", -2.0, "Existenz"), ("bankruptcy", -2.0, "Existenz"),
    ("chapter 11", -2.0, "Existenz"), ("pleite", -2.0, "Existenz"), ("going concern", -1.5, "Existenz"),
    ("delisting", -1.5, "Existenz"), ("fraud", -1.5, "Rechtliches"), ("betrug", -1.5, "Rechtliches"),
    ("bilanzskandal", -1.5, "Rechtliches"), ("investigation", -0.6, "Rechtliches"), ("ermittlung", -0.6, "Rechtliches"),
    ("razzia", -0.8, "Rechtliches"), ("lawsuit", -0.4, "Rechtliches"), ("klage", -0.3, "Rechtliches"),
    ("short seller", -0.8, "Rechtliches"), ("leerverkäufer", -0.8, "Rechtliches"),
    ("layoffs", -0.2, "Restrukturierung"), ("stellenabbau", -0.2, "Restrukturierung"),
    ("plunge", -0.6, "Markt"), ("plummets", -0.6, "Markt"), ("tumbles", -0.5, "Markt"), ("slump", -0.5, "Markt"),
    ("bricht ein", -0.6, "Markt"), ("absturz", -0.6, "Markt"), ("stürzt", -0.6, "Markt"), ("fällt", -0.2, "Markt"),
    ("crash", -0.6, "Markt"), ("verkaufssignal", -0.5, "Markt"), ("bergab", -0.4, "Markt"),
    ("kursrutsch", -0.6, "Markt"), ("abwärtstrend", -0.4, "Markt"), ("jahrestief", -0.3, "Markt"),
    ("rekordtief", -0.5, "Markt"), ("allzeittief", -0.5, "Markt"), ("rückruf", -0.5, "Geschäft"), ("recall", -0.5, "Geschäft"),
    ("strike", -0.3, "Geschäft"), ("streik", -0.3, "Geschäft"),
]

RED_FLAG_CATEGORIES = {"Existenz"}
TURNAROUND_CATEGORIES = {"Turnaround", "Restrukturierung", "Management"}


def score_text(text: str) -> tuple[float, list[str], list[str]]:
    """Gibt (Rohscore, Treffer, Kategorien) für einen Text zurück."""
    t = text.lower()
    score, hits, cats = 0.0, [], []
    for pattern, weight, cat in LEXICON:
        if pattern in t:
            # "order" etc. nur als ganzes Wort werten
            if len(pattern) <= 6 and not re.search(rf"\b{re.escape(pattern)}", t):
                continue
            score += weight
            hits.append(pattern)
            cats.append(cat)
    return score, hits, cats


def _parse_yahoo(items: list, ticker: str = "", keyword: str = "") -> list[dict]:
    """Yahoo liefert teils Meldungen zu anderen Firmen -> nur relevante behalten."""
    rows = []
    base = ticker.split(".")[0].upper()
    for it in items or []:
        c = it.get("content", it) if isinstance(it, dict) else {}
        title = c.get("title") or ""
        if not title:
            continue
        ts = c.get("pubDate") or c.get("displayTime")
        try:
            dt = pd.to_datetime(ts, utc=True) if ts else pd.NaT
        except Exception:
            dt = pd.NaT
        if pd.isna(dt) and it.get("providerPublishTime"):
            dt = pd.to_datetime(it["providerPublishTime"], unit="s", utc=True)
        symbols = {str(x.get("symbol", "")).upper().split(".")[0]
                   for x in ((c.get("finance") or {}).get("stockTickers") or [])}
        text = f"{title} {c.get('summary') or ''}".lower()
        if not (base in symbols or (keyword and keyword.lower() in text)):
            continue
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or it.get("link") or "")
        rows.append({
            "Datum": dt, "Titel": title, "Zusammenfassung": c.get("summary") or "",
            "Quelle": (c.get("provider") or {}).get("displayName", "Yahoo Finance"), "Link": url, "Herkunft": "Yahoo",
        })
    return rows


def fetch_google_news(query: str, after: str | None = None, before: str | None = None,
                      lang: str = "de", limit: int = 40) -> list[dict]:
    q = query
    if after:
        q += f" after:{after}"
    if before:
        q += f" before:{before}"
    region = "DE" if lang == "de" else "US"
    url = (f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}"
           f"&hl={lang}&gl={region}&ceid={region}:{lang}")
    try:
        feed = feedparser.parse(url, agent="Mozilla/5.0 (TurnaroundScanner)")
    except Exception:
        return []
    rows = []
    for e in feed.entries[:limit]:
        title = html.unescape(e.get("title", ""))
        source = (e.get("source") or {}).get("title", "Google News")
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            dt = pd.to_datetime(e.get("published"), utc=True)
        except Exception:
            dt = pd.NaT
        summary = re.sub(r"<[^>]+>", " ", html.unescape(e.get("summary", "")))
        rows.append({"Datum": dt, "Titel": title, "Zusammenfassung": "", "Quelle": source,
                     "Link": e.get("link", ""), "Herkunft": "Google News", "_summary": summary})
    return rows


def _company_query(name: str | None, ticker: str) -> str:
    if not name:
        return ticker.split(".")[0]
    name = re.sub(r"\s+", " ", name).strip()
    # Rechtsformen entfernen, damit die Suche mehr trifft
    name = re.sub(r"\b(SE|AG|N\.?V\.?|S\.?A\.?|plc|PLC|Inc\.?|Corp\.?|Corporation|Ltd\.?|KGaA|& Co\.?|"
                  r"Holding|Group|Aktiengesellschaft|Co\.|SpA|S\.p\.A\.|ASA|A/S|Oyj|AB|NA|O\.N\.|I|VZ|ST)\b\.?",
                  " ", name)
    name = re.sub(r"\s+", " ", name).strip(" ,.-")
    return f'"{name}"' if name else ticker


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    scored = [score_text(f"{r.Titel} {r.Zusammenfassung}") for r in df.itertuples()]
    df = df.copy()
    df["Stimmung"] = [s[0] for s in scored]
    df["Treffer"] = [", ".join(dict.fromkeys(s[1])) for s in scored]
    df["Kategorien"] = [", ".join(dict.fromkeys(s[2])) for s in scored]
    return df


def load_news(ticker: str, name: str | None = None, max_items: int = 60) -> pd.DataFrame:
    key = f"news:{ticker}"
    cached = cache_get(key, NEWS_TTL_H)
    if cached is not None:
        return cached
    rows: list[dict] = []
    q = _company_query(name, ticker)
    keyword = q.strip('"').split(" ")[0] if q.startswith('"') else ""
    try:
        rows += _parse_yahoo(yf.Ticker(ticker).news, ticker, keyword)
    except Exception:
        pass
    rows += fetch_google_news(f"{q} Aktie", lang="de", limit=30)
    if not ticker.endswith((".DE", ".F", ".VI", ".SW")):
        rows += fetch_google_news(f"{q} stock", lang="en", limit=20)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop(columns=["_summary"], errors="ignore")
        df = df.drop_duplicates(subset="Titel").sort_values("Datum", ascending=False).head(max_items)
        df = annotate(df).reset_index(drop=True)
    cache_set(key, df)
    return df


def summarize_news(df: pd.DataFrame, now: datetime | None = None) -> dict:
    """Verdichtet Nachrichten zu Kennzahlen für das Scoring."""
    if df is None or df.empty:
        return {"news_count": 0, "news_sentiment": np.nan, "news_turnaround_hits": 0,
                "news_red_flags": [], "news_categories": {}}
    now = now or datetime.now(timezone.utc)
    dates = pd.to_datetime(df["Datum"], utc=True)
    age = (pd.Timestamp(now) - dates).dt.total_seconds().fillna(90 * 86400) / 86400
    w = np.power(0.5, age.clip(lower=0) / HALF_LIFE_DAYS)
    recent = age <= 120
    s = df["Stimmung"].clip(-2, 2)
    sentiment = float(np.tanh((s * w).sum() / max(w.sum(), 1e-9)))
    cats_series = df.loc[recent, "Kategorien"].str.split(", ").explode()
    cats_series = cats_series[cats_series.astype(bool)] if len(cats_series) else cats_series
    cat_counts = cats_series.value_counts().to_dict() if len(cats_series) else {}
    red = df.loc[recent & df["Kategorien"].str.contains("|".join(RED_FLAG_CATEGORIES), na=False), "Titel"].tolist()
    ta_hits = int(sum(cat_counts.get(c, 0) for c in TURNAROUND_CATEGORIES))
    return {"news_count": int(len(df)), "news_sentiment": sentiment, "news_turnaround_hits": ta_hits,
            "news_red_flags": red[:5], "news_categories": cat_counts}


def load_news_bulk(items: list[tuple[str, str | None]], progress: ProgressFn = None,
                   workers: int = 6) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(load_news, t, n): t for t, n in items}
        for f in as_completed(futs):
            t = futs[f]
            try:
                out[t] = f.result()
            except Exception:
                out[t] = pd.DataFrame()
            done += 1
            if progress:
                progress(done / max(len(items), 1), f"Nachrichten {done}/{len(items)}: {t}")
    return out


def news_around(ticker: str, name: str | None, date: pd.Timestamp, days: int = 45) -> pd.DataFrame:
    """Historische Nachrichten rund um einen Wendepunkt (Google-News-Zeitfilter)."""
    after = (date - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    before = (date + pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    key = f"newsaround:{ticker}:{after}:{before}"
    cached = cache_get(key, 24 * 7)
    if cached is not None:
        return cached
    q = _company_query(name, ticker)
    rows = fetch_google_news(f"{q} Aktie", after=after, before=before, lang="de", limit=30)
    rows += fetch_google_news(f"{q} stock", after=after, before=before, lang="en", limit=20)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop(columns=["_summary"], errors="ignore").drop_duplicates(subset="Titel")
        df = annotate(df.sort_values("Datum")).reset_index(drop=True)
    cache_set(key, df)
    return df
