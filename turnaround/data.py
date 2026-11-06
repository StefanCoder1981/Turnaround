"""Datenbeschaffung über Yahoo Finance (yfinance) mit Festplatten-Cache."""
from __future__ import annotations

import hashlib
import logging
import pickle
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

PRICE_TTL_H = 12
FUNDAMENTAL_TTL_H = 24

ProgressFn = Callable[[float, str], None] | None


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def _cache_path(key: str) -> Path:
    safe = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{safe}.pkl"


def cache_get(key: str, max_age_h: float):
    p = _cache_path(key)
    if not p.exists() or (time.time() - p.stat().st_mtime) > max_age_h * 3600:
        return None
    try:
        with p.open("rb") as fh:
            return pickle.load(fh)
    except Exception:  # beschädigter Cache -> neu laden
        return None


def cache_set(key: str, obj) -> None:
    try:
        with _cache_path(key).open("wb") as fh:
            pickle.dump(obj, fh)
    except Exception as exc:  # pragma: no cover
        log.warning("Cache schreiben fehlgeschlagen: %s", exc)


def clear_cache() -> int:
    n = 0
    for p in CACHE_DIR.glob("*.pkl"):
        p.unlink(missing_ok=True)
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Kurse
# --------------------------------------------------------------------------- #
def load_prices(tickers: list[str], years: int = 6, progress: ProgressFn = None) -> pd.DataFrame:
    """Lädt bereinigte Schlusskurse (Spalten = Ticker), nutzt Cache je Ticker."""
    result: dict[str, pd.Series] = {}
    missing: list[str] = []
    for t in tickers:
        s = cache_get(f"price:{t}:{years}", PRICE_TTL_H)
        if s is None:
            missing.append(t)
        elif len(s):
            result[t] = s

    chunk = 25
    for i in range(0, len(missing), chunk):
        part = missing[i:i + chunk]
        if progress:
            progress(i / max(len(missing), 1), f"Lade Kurse {i + 1}–{i + len(part)} von {len(missing)} …")
        try:
            df = yf.download(part, period=f"{years}y", auto_adjust=True, progress=False,
                             threads=True, group_by="column")
        except Exception as exc:
            log.warning("Kursdownload fehlgeschlagen: %s", exc)
            df = pd.DataFrame()
        close = _extract_close(df, part)
        for t in part:
            s = close.get(t)
            s = s.dropna() if s is not None else pd.Series(dtype=float)
            if len(s):
                s.index = pd.to_datetime(s.index).tz_localize(None)
                s.name = t
                result[t] = s
            cache_set(f"price:{t}:{years}", s)  # leere Serie = Ticker ungültig (cache trotzdem)
    if progress:
        progress(1.0, "Kurse geladen")
    if not result:
        return pd.DataFrame()
    return pd.DataFrame(result).sort_index()


def _extract_close(df: pd.DataFrame, tickers: list[str]) -> dict[str, pd.Series]:
    if df is None or df.empty:
        return {}
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):
            c = df["Close"]
            return {t: c[t] for t in c.columns}
        return {}
    if "Close" in df.columns and len(tickers) == 1:
        return {tickers[0]: df["Close"]}
    return {}


# --------------------------------------------------------------------------- #
# Fundamentaldaten
# --------------------------------------------------------------------------- #
INFO_FIELDS = [
    "shortName", "longName", "sector", "industry", "country", "currency", "marketCap",
    "priceToBook", "enterpriseToRevenue", "enterpriseToEbitda", "trailingPE", "forwardPE",
    "trailingEps", "forwardEps", "debtToEquity", "recommendationMean", "recommendationKey",
    "numberOfAnalystOpinions", "targetMeanPrice", "currentPrice", "regularMarketPrice",
    "revenueGrowth", "earningsGrowth", "profitMargins", "operatingMargins",
    "shortPercentOfFloat", "heldPercentInsiders", "dividendYield", "beta",
    "longBusinessSummary", "website",
]


def _row(df: pd.DataFrame | None, names: list[str]) -> pd.Series | None:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = pd.to_numeric(df.loc[n], errors="coerce")
            if isinstance(s, pd.DataFrame):
                s = s.iloc[0]
            s.index = pd.to_datetime(s.index)
            s = s.sort_index().dropna()
            if len(s):
                return s
    return None


REV = ["Total Revenue", "Operating Revenue"]
OPI = ["Operating Income", "Total Operating Income As Reported", "EBIT"]
NI = ["Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation Net Minority Interest"]
FCF = ["Free Cash Flow"]
DEBT = ["Total Debt"]
EQ = ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"]


def _safe_ratio(a, b):
    try:
        if b is None or a is None or b == 0 or np.isnan(a) or np.isnan(b):
            return np.nan
        return a / b
    except Exception:
        return np.nan


def _yoy(s: pd.Series | None, lag: int) -> tuple[float, float]:
    """Wert jetzt und vor `lag` Perioden (oder ältester verfügbarer)."""
    if s is None or len(s) < 2:
        return np.nan, np.nan
    prev_idx = max(0, len(s) - 1 - lag)
    return float(s.iloc[-1]), float(s.iloc[prev_idx])


def derive_fundamentals(q_inc, q_cf, q_bs, a_inc, a_cf, a_bs) -> dict:
    """Leitet Turnaround-relevante Kennzahlen aus Abschlüssen ab."""
    out: dict = {}
    rev_q, opi_q, ni_q = _row(q_inc, REV), _row(q_inc, OPI), _row(q_inc, NI)
    fcf_q, debt_q, eq_q = _row(q_cf, FCF), _row(q_bs, DEBT), _row(q_bs, EQ)
    rev_a, opi_a, ni_a = _row(a_inc, REV), _row(a_inc, OPI), _row(a_inc, NI)
    fcf_a, debt_a, eq_a = _row(a_cf, FCF), _row(a_bs, DEBT), _row(a_bs, EQ)

    # Operative Marge: Quartal ggü. Vorjahresquartal, sonst Jahresvergleich
    margin_now = margin_prev = np.nan
    if rev_q is not None and opi_q is not None:
        m = (opi_q / rev_q.reindex(opi_q.index)).dropna()
        m = m[np.isfinite(m)]
        if len(m) >= 5:
            margin_now, margin_prev = float(m.iloc[-1]), float(m.iloc[-5])
        elif len(m) >= 2:
            margin_now, margin_prev = _yoy(m, 1)
    if np.isnan(margin_now) and rev_a is not None and opi_a is not None:
        m = (opi_a / rev_a.reindex(opi_a.index)).dropna()
        m = m[np.isfinite(m)]
        if len(m) >= 2:
            margin_now, margin_prev = float(m.iloc[-1]), float(m.iloc[-2])
    out["op_margin"] = margin_now
    out["margin_trend"] = margin_now - margin_prev if not np.isnan(margin_now) else np.nan

    # Umsatzwachstum
    g = np.nan
    if rev_q is not None and len(rev_q) >= 5:
        g = _safe_ratio(rev_q.iloc[-1], rev_q.iloc[-5]) - 1
    elif rev_a is not None and len(rev_a) >= 2:
        g = _safe_ratio(rev_a.iloc[-1], rev_a.iloc[-2]) - 1
    out["rev_growth"] = g

    # Nettogewinn: TTM und Wende von Verlust zu Gewinn
    ni_ttm = float(ni_q.iloc[-4:].sum()) if ni_q is not None and len(ni_q) >= 4 else (
        float(ni_a.iloc[-1]) if ni_a is not None and len(ni_a) else np.nan)
    out["ni_ttm"] = ni_ttm
    had_loss = bool(ni_a is not None and (ni_a.iloc[:-1] < 0).any()) or bool(
        ni_q is not None and len(ni_q) > 4 and (ni_q.iloc[:-4] < 0).any())
    out["had_loss"] = had_loss
    out["ni_turn"] = bool(had_loss and not np.isnan(ni_ttm) and ni_ttm > 0)
    ni_prev = float(ni_a.iloc[-2]) if ni_a is not None and len(ni_a) >= 2 else np.nan
    out["ni_improving"] = bool(not np.isnan(ni_ttm) and not np.isnan(ni_prev) and ni_ttm > ni_prev)

    # Free Cashflow
    fcf_ttm = float(fcf_q.iloc[-4:].sum()) if fcf_q is not None and len(fcf_q) >= 4 else (
        float(fcf_a.iloc[-1]) if fcf_a is not None and len(fcf_a) else np.nan)
    fcf_prev = float(fcf_a.iloc[-2]) if fcf_a is not None and len(fcf_a) >= 2 else np.nan
    out["fcf_ttm"] = fcf_ttm
    out["fcf_improving"] = bool(not np.isnan(fcf_ttm) and not np.isnan(fcf_prev) and fcf_ttm > fcf_prev)

    # Verschuldung
    d_now, d_prev = _yoy(debt_q, 4)
    if np.isnan(d_now):
        d_now, d_prev = _yoy(debt_a, 1)
    out["debt_change"] = _safe_ratio(d_now, d_prev) - 1 if d_prev and d_prev > 0 else np.nan
    e_now = float(eq_q.iloc[-1]) if eq_q is not None and len(eq_q) else (
        float(eq_a.iloc[-1]) if eq_a is not None and len(eq_a) else np.nan)
    out["equity"] = e_now
    out["negative_equity"] = bool(not np.isnan(e_now) and e_now < 0)

    # Jahreshistorie für Charts
    hist = pd.DataFrame({
        "Umsatz": rev_a, "Operatives Ergebnis": opi_a, "Nettogewinn": ni_a, "Free Cashflow": fcf_a,
        "Schulden": debt_a,
    }) if any(x is not None for x in (rev_a, opi_a, ni_a)) else pd.DataFrame()
    if not hist.empty:
        hist.index = hist.index.year
        if "Umsatz" in hist and "Operatives Ergebnis" in hist:
            hist["Operative Marge"] = hist["Operatives Ergebnis"] / hist["Umsatz"]
    out["history"] = hist
    return out


def load_fundamental(ticker: str) -> dict:
    key = f"fund:{ticker}"
    cached = cache_get(key, FUNDAMENTAL_TTL_H)
    if cached is not None:
        return cached
    tk = yf.Ticker(ticker)
    info: dict = {}
    try:
        raw = tk.info or {}
        info = {k: raw.get(k) for k in INFO_FIELDS}
    except Exception as exc:
        log.info("info für %s fehlgeschlagen: %s", ticker, exc)

    def _get(attr):
        try:
            return getattr(tk, attr)
        except Exception:
            return None

    derived = derive_fundamentals(
        _get("quarterly_income_stmt"), _get("quarterly_cashflow"), _get("quarterly_balance_sheet"),
        _get("income_stmt"), _get("cashflow"), _get("balance_sheet"),
    )
    res = {"ticker": ticker, "info": info, **derived}
    cache_set(key, res)
    return res


def load_fundamentals(tickers: list[str], progress: ProgressFn = None, workers: int = 6) -> dict[str, dict]:
    out: dict[str, dict] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(load_fundamental, t): t for t in tickers}
        for f in as_completed(futs):
            t = futs[f]
            try:
                out[t] = f.result()
            except Exception as exc:
                log.warning("Fundamentaldaten %s: %s", t, exc)
                out[t] = {"ticker": t, "info": {}, "history": pd.DataFrame()}
            done += 1
            if progress:
                progress(done / len(tickers), f"Fundamentaldaten {done}/{len(tickers)}: {t}")
    return out
