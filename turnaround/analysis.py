"""Turnaround-Erkennung (historisch) und Scoring möglicher künftiger Turnarounds."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class Params:
    lookback_years: int = 5
    hist_min_drawdown: float = 0.40   # historischer Turnaround: Mindesteinbruch
    hist_min_recovery: float = 0.60   # ... und Mindesterholung vom Tief
    cand_min_drawdown: float = 0.30   # Kandidat: aktueller Abstand zum Hoch
    min_market_cap: float = 300e6
    weights: dict = field(default_factory=lambda: {
        "Abschlag": 1.0, "Bodenbildung": 1.5, "Momentum": 1.5,
        "Fundamental": 2.0, "Bewertung": 1.0, "News": 1.0,
    })


FACTOR_HELP = {
    "Abschlag": "Wie tief liegt die Aktie unter ihrem Hoch im Analysezeitraum? Tiefer = mehr Erholungspotenzial.",
    "Bodenbildung": "Erholung vom Tief, Zeit seit dem Tief und sinkende Volatilität – Zeichen, dass der Ausverkauf endet.",
    "Momentum": "Kurs über 50/200-Tage-Linie, steigende Durchschnitte, positive 3-Monats-Rendite.",
    "Fundamental": "Margenverbesserung, Umsatzwachstum, Rückkehr in die Gewinnzone, Free Cashflow, Schuldenabbau, steigende EPS-Erwartung.",
    "Bewertung": "Kurs-Buchwert, EV/Umsatz und Kursziel-Potenzial laut Analysten.",
    "News": "Zeitgewichtete Stimmung aktueller Nachrichten inkl. Turnaround-Stichworten (neuer CEO, Restrukturierung, Prognose angehoben …).",
}


def _clip(x, lo=0.0, hi=1.0):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return np.nan
    return float(min(hi, max(lo, x)))


def _nanmean(vals):
    v = [x for x in vals if x is not None and not (isinstance(x, float) and np.isnan(x))]
    return float(np.mean(v)) if v else np.nan


# --------------------------------------------------------------------------- #
# Historische Turnarounds
# --------------------------------------------------------------------------- #
def detect_turnaround(close: pd.Series, min_dd: float = 0.4, min_rec: float = 0.6) -> dict | None:
    """Findet die stärkste Erholung nach einem Einbruch von mindestens `min_dd`.

    Für jeden Tag t: Einbruch = Kurs_t / max(Kurs bis t) − 1,
    Erholung = max(Kurs ab t) / Kurs_t − 1. Gewählt wird der Tag mit maximaler
    Erholung unter allen Tagen, deren Einbruch die Schwelle erreicht.
    """
    s = close.dropna()
    if len(s) < 120:
        return None
    vals = s.to_numpy(dtype=float)
    run_max = np.maximum.accumulate(vals)
    fwd_max = np.maximum.accumulate(vals[::-1])[::-1]
    dd = vals / run_max - 1
    rec = fwd_max / vals - 1
    mask = dd <= -min_dd
    if not mask.any():
        i = int(np.argmin(dd))
        return {"Status": "Kein tiefer Einbruch", "Einbruch": float(dd[i]), "Erholung": float(rec[i]),
                "Tief-Datum": s.index[i], "Tief": float(vals[i]), "Turnaround": False}
    cand = np.where(mask, rec, -np.inf)
    i = int(np.argmax(cand))
    peak_i = int(np.argmax(vals[: i + 1]))
    post_i = i + int(np.argmax(vals[i:]))
    days_since_trough = (s.index[-1] - s.index[i]).days
    recovered_share = (vals[post_i] - vals[i]) / max(vals[peak_i] - vals[i], 1e-9)
    is_ta = rec[i] >= min_rec
    if is_ta and recovered_share >= 1:
        status = "Erfolgreich (neues Hoch)"
    elif is_ta:
        status = "Erfolgreich"
    elif rec[i] >= 0.25 and days_since_trough >= 60:
        status = "Laufend"
    else:
        status = "Ohne Erholung"
    return {
        "Status": status, "Turnaround": bool(is_ta),
        "Hoch-Datum": s.index[peak_i], "Hoch": float(vals[peak_i]),
        "Tief-Datum": s.index[i], "Tief": float(vals[i]),
        "Erholungs-Hoch-Datum": s.index[post_i], "Erholungs-Hoch": float(vals[post_i]),
        "Einbruch": float(dd[i]), "Erholung": float(rec[i]),
        "Anteil Verlust aufgeholt": float(recovered_share),
        "Abwärtsphase (Tage)": int((s.index[i] - s.index[peak_i]).days),
        "Erholungsphase (Tage)": int((s.index[post_i] - s.index[i]).days),
        "Aktuell vs. Tief": float(vals[-1] / vals[i] - 1),
        "Aktuell vs. Hoch": float(vals[-1] / vals[peak_i] - 1),
    }


# --------------------------------------------------------------------------- #
# Kurs-Signale
# --------------------------------------------------------------------------- #
def price_signals(close: pd.Series) -> dict | None:
    s = close.dropna()
    if len(s) < 260:
        return None
    last = float(s.iloc[-1])
    peak_pos = int(np.argmax(s.to_numpy()))
    high = float(s.iloc[peak_pos])
    after_peak = s.iloc[peak_pos:]
    low_pos = int(np.argmin(after_peak.to_numpy()))
    low = float(after_peak.iloc[low_pos])
    low_date = after_peak.index[low_pos]
    sma50, sma200 = s.rolling(50).mean(), s.rolling(200).mean()
    rets = np.log(s).diff()
    vol60 = float(rets.iloc[-60:].std() * np.sqrt(TRADING_DAYS))
    vol250 = float(rets.iloc[-250:].std() * np.sqrt(TRADING_DAYS))

    def ret(n):
        return float(last / s.iloc[-n - 1] - 1) if len(s) > n else np.nan

    return {
        "Kurs": last, "Hoch": high, "Hoch-Datum": s.index[peak_pos],
        "Tief seit Hoch": low, "Tief-Datum": low_date,
        "Abstand Hoch": last / high - 1, "Erholung vom Tief": last / low - 1,
        "Tage seit Tief": int((s.index[-1] - low_date).days),
        "SMA50": float(sma50.iloc[-1]), "SMA200": float(sma200.iloc[-1]),
        "SMA50 Steigung": float(sma50.iloc[-1] / sma50.iloc[-21] - 1),
        "SMA200 Steigung": float(sma200.iloc[-1] / sma200.iloc[-41] - 1),
        "Rendite 1M": ret(21), "Rendite 3M": ret(63), "Rendite 6M": ret(126), "Rendite 12M": ret(252),
        "Vola 60T": vol60, "Vola 1J": vol250, "Vola-Verhältnis": vol60 / vol250 if vol250 else np.nan,
    }


def score_discount(ps: dict) -> float:
    return _clip((-ps["Abstand Hoch"] - 0.15) / 0.45)


def score_stabilization(ps: dict) -> float:
    r = ps["Erholung vom Tief"]
    if r < 0.05:
        rb = 0.1
    elif r < 0.20:
        rb = 0.1 + 0.9 * (r - 0.05) / 0.15
    elif r <= 0.70:
        rb = 1.0
    else:
        rb = max(0.4, 1.0 - 0.6 * (r - 0.70) / 0.8)
    days = _clip(ps["Tage seit Tief"] / 150)
    vr = _clip((1.3 - ps["Vola-Verhältnis"]) / 0.6) if not np.isnan(ps["Vola-Verhältnis"]) else 0.5
    return _nanmean([rb, rb, days, vr])


def score_momentum(ps: dict) -> float:
    last = ps["Kurs"]
    above50 = 1.0 if last > ps["SMA50"] else _clip(1 + (last / ps["SMA50"] - 1) / 0.1)
    above200 = 1.0 if last > ps["SMA200"] else _clip(1 + (last / ps["SMA200"] - 1) / 0.2)
    slope50 = _clip(0.5 + ps["SMA50 Steigung"] / 0.08)
    slope200 = _clip(0.5 + ps["SMA200 Steigung"] / 0.08)
    r3 = _clip(0.5 + ps["Rendite 3M"] / 0.3)
    return _nanmean([above50, above200, slope50, slope200, r3])


def score_fundamental(f: dict) -> tuple[float, int]:
    info = f.get("info") or {}
    parts = []
    mt = f.get("margin_trend", np.nan)
    parts.append(_clip(0.5 + mt / 0.10) if mt is not None and not np.isnan(mt) else np.nan)
    g = f.get("rev_growth", np.nan)
    parts.append(_clip(0.5 + g / 0.30) if g is not None and not np.isnan(g) else np.nan)
    ni = f.get("ni_ttm", np.nan)
    if ni is not None and not np.isnan(ni):
        if f.get("ni_turn"):
            parts.append(1.0)
        elif ni > 0:
            parts.append(0.75 if f.get("ni_improving") else 0.6)
        else:
            parts.append(0.4 if f.get("ni_improving") else 0.1)
    fcf = f.get("fcf_ttm", np.nan)
    if fcf is not None and not np.isnan(fcf):
        parts.append((0.8 if fcf > 0 else 0.2) + (0.2 if f.get("fcf_improving") else 0.0))
    dc = f.get("debt_change", np.nan)
    parts.append(_clip(0.5 - dc / 0.4) if dc is not None and not np.isnan(dc) else np.nan)
    te, fe = info.get("trailingEps"), info.get("forwardEps")
    if te is not None and fe is not None:
        try:
            te, fe = float(te), float(fe)
            if te <= 0 < fe:
                parts.append(1.0)
            elif te > 0:
                parts.append(_clip(0.5 + (fe / te - 1)))
            else:
                parts.append(_clip(0.5 + (fe - te) / abs(te) / 2))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    n = sum(1 for p in parts if p is not None and not np.isnan(p))
    return _nanmean(parts), n


def score_valuation(f: dict, last_price: float) -> float:
    info = f.get("info") or {}
    parts = []
    pb = info.get("priceToBook")
    if isinstance(pb, (int, float)) and pb > 0:
        parts.append(_clip((3.0 - pb) / 2.5))
    evs = info.get("enterpriseToRevenue")
    if isinstance(evs, (int, float)) and evs > 0:
        parts.append(_clip((4.0 - evs) / 3.5))
    tgt = info.get("targetMeanPrice")
    cur = info.get("currentPrice") or info.get("regularMarketPrice") or last_price
    if isinstance(tgt, (int, float)) and cur:
        parts.append(_clip(0.5 + (tgt / cur - 1) / 0.6))
    rec = info.get("recommendationMean")  # 1 = Strong Buy … 5 = Sell
    if isinstance(rec, (int, float)):
        parts.append(_clip((4.0 - rec) / 2.5))
    return _nanmean(parts)


def score_news(ns: dict | None) -> float:
    if not ns or not ns.get("news_count"):
        return np.nan
    base = (ns["news_sentiment"] + 1) / 2
    bonus = min(0.15, 0.03 * ns.get("news_turnaround_hits", 0))
    return _clip(base + bonus)


def phase_label(ps: dict, momentum: float) -> str:
    dd, reb = ps["Abstand Hoch"], ps["Erholung vom Tief"]
    if dd > -0.15:
        return "Nahe Hoch"
    if dd <= -0.30:
        if momentum < 0.35:
            return "Abwärtstrend"
        if reb < 0.25:
            return "Bodenbildung"
        return "Frühe Erholung"
    return "Turnaround läuft" if reb > 0.30 else "Seitwärts / Korrektur"


def risk_flags(ps: dict, f: dict, ns: dict | None) -> list[str]:
    flags = []
    info = f.get("info") or {}
    if ps["Kurs"] < 0.8 * ps["SMA200"] and ps["SMA200 Steigung"] < 0 and ps["Rendite 3M"] < -0.10:
        flags.append("Fallendes Messer")
    if f.get("negative_equity"):
        flags.append("Negatives Eigenkapital")
    de = info.get("debtToEquity")
    if isinstance(de, (int, float)) and de > 300:
        flags.append("Hohe Verschuldung")
    fcf = f.get("fcf_ttm")
    if fcf is not None and not np.isnan(fcf) and fcf < 0 and not f.get("fcf_improving"):
        flags.append("Cash-Burn")
    if ns and ns.get("news_red_flags"):
        flags.append("Existenz-Nachrichten")
    return flags


FLAG_PENALTY = {"Fallendes Messer": 0.75, "Negatives Eigenkapital": 0.85, "Hohe Verschuldung": 0.9,
                "Cash-Burn": 0.9, "Existenz-Nachrichten": 0.5}


def clean_name(info: dict, ticker: str) -> str:
    name = info.get("longName") or info.get("shortName") or ticker
    name = re.split(r"\s{2,}", name.strip())[0]  # Yahoo füllt Kurznamen mit Leerzeichen + Gattung auf
    return name or ticker


def combine(sub: dict, weights: dict) -> float:
    num = den = 0.0
    for k, w in weights.items():
        v = sub.get(k)
        if v is None or np.isnan(v) or w <= 0:
            continue
        num += w * v
        den += w
    return 100 * num / den if den else np.nan


# --------------------------------------------------------------------------- #
# Gesamt-Pipeline
# --------------------------------------------------------------------------- #
def analyze_universe(prices: pd.DataFrame, fundamentals: dict[str, dict],
                     news_summary: dict[str, dict], params: Params) -> pd.DataFrame:
    start = prices.index.max() - pd.DateOffset(years=params.lookback_years)
    px = prices.loc[prices.index >= start]
    rows = []
    for t in px.columns:
        close = px[t].dropna()
        ps = price_signals(close)
        if ps is None:
            continue
        f = fundamentals.get(t, {}) or {}
        info = f.get("info") or {}
        ns = news_summary.get(t)
        ta = detect_turnaround(close, params.hist_min_drawdown, params.hist_min_recovery) or {}
        fund, fund_n = score_fundamental(f)
        sub = {
            "Abschlag": score_discount(ps), "Bodenbildung": score_stabilization(ps),
            "Momentum": score_momentum(ps), "Fundamental": fund,
            "Bewertung": score_valuation(f, ps["Kurs"]), "News": score_news(ns),
        }
        flags = risk_flags(ps, f, ns)
        raw = combine(sub, params.weights)
        penalty = float(np.prod([FLAG_PENALTY.get(fl, 1.0) for fl in flags])) if flags else 1.0
        rows.append({
            "Ticker": t,
            "Name": clean_name(info, t),
            "Sektor": info.get("sector") or "Unbekannt",
            "Branche": info.get("industry") or "",
            "Währung": info.get("currency") or "",
            "Marktkap.": info.get("marketCap"),
            **{k: ps[k] for k in ["Kurs", "Hoch", "Hoch-Datum", "Tief seit Hoch", "Tief-Datum", "Abstand Hoch",
                                  "Erholung vom Tief", "Tage seit Tief", "Rendite 1M", "Rendite 3M",
                                  "Rendite 6M", "Rendite 12M", "Vola 1J", "SMA50", "SMA200"]},
            "Phase": phase_label(ps, sub["Momentum"]),
            **{f"S_{k}": v for k, v in sub.items()},
            "Fundamental-Abdeckung": fund_n,
            "Score roh": raw, "Score": raw * penalty if not np.isnan(raw) else np.nan,
            "Risiken": ", ".join(flags),
            "Margentrend": f.get("margin_trend"), "Umsatzwachstum": f.get("rev_growth"),
            "Gewinnwende": f.get("ni_turn"), "Nettogewinn TTM": f.get("ni_ttm"),
            "FCF TTM": f.get("fcf_ttm"), "Schuldenänderung": f.get("debt_change"),
            "KBV": info.get("priceToBook"), "EV/Umsatz": info.get("enterpriseToRevenue"),
            "KGV fwd": info.get("forwardPE"), "Kursziel": info.get("targetMeanPrice"),
            "Analysten": info.get("recommendationKey"),
            "News-Anzahl": (ns or {}).get("news_count", 0), "News-Stimmung": (ns or {}).get("news_sentiment"),
            "Turnaround-Stichworte": (ns or {}).get("news_turnaround_hits", 0),
            # historische Erkennung
            "Hist. Status": ta.get("Status"), "Hist. Turnaround": ta.get("Turnaround", False),
            "Hist. Einbruch": ta.get("Einbruch"), "Hist. Erholung": ta.get("Erholung"),
            "Hist. Hoch-Datum": ta.get("Hoch-Datum"), "Hist. Tief-Datum": ta.get("Tief-Datum"),
            "Hist. Erholungs-Hoch-Datum": ta.get("Erholungs-Hoch-Datum"),
            "Hist. Abwärtsphase (Tage)": ta.get("Abwärtsphase (Tage)"),
            "Hist. Erholungsphase (Tage)": ta.get("Erholungsphase (Tage)"),
            "Hist. Anteil aufgeholt": ta.get("Anteil Verlust aufgeholt"),
            "Fundamental bestätigt": bool(f.get("ni_turn") or (f.get("margin_trend") or 0) > 0.02),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["Kandidat"] = (df["Abstand Hoch"] <= -params.cand_min_drawdown) & (
        df["Marktkap."].fillna(np.inf) >= params.min_market_cap)
    return df.sort_values("Score", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Signal-Check: rollierender Test der reinen Kurs-Signale
# --------------------------------------------------------------------------- #
PRICE_FACTORS = ("Abschlag", "Bodenbildung", "Momentum")


def price_only_score(close: pd.Series, weights: dict) -> tuple[float, float] | None:
    ps = price_signals(close)
    if ps is None:
        return None
    sub = {"Abschlag": score_discount(ps), "Bodenbildung": score_stabilization(ps), "Momentum": score_momentum(ps)}
    flags = ["Fallendes Messer"] if (ps["Kurs"] < 0.8 * ps["SMA200"] and ps["SMA200 Steigung"] < 0
                                    and ps["Rendite 3M"] < -0.10) else []
    w = {k: weights.get(k, 1.0) for k in PRICE_FACTORS}
    sc = combine(sub, w) * (FLAG_PENALTY["Fallendes Messer"] if flags else 1.0)
    return sc, ps["Abstand Hoch"]


def signal_backtest(prices: pd.DataFrame, params: Params, horizon_months: int = 12,
                    top_n: int = 10, step_months: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Wendet die Kurs-Signale an vergangenen Stichtagen an und misst die Folgerendite.

    Hinweis: Fundamental- und News-Daten sind nicht historisch verfügbar und
    fließen daher nicht ein. Survivorship-Bias: Das Universum besteht aus heute
    gelisteten Titeln.
    """
    idx = prices.index
    first = idx.min() + pd.DateOffset(years=2)
    last = idx.max() - pd.DateOffset(months=horizon_months)
    dates = []
    d = last
    while d >= first:
        dates.append(d)
        d -= pd.DateOffset(months=step_months)
    dates = sorted(dates)
    period_rows, detail_rows = [], []
    for d in dates:
        hist = prices.loc[(idx <= d) & (idx > d - pd.DateOffset(years=params.lookback_years))]
        end = d + pd.DateOffset(months=horizon_months)
        fwd_px = prices.loc[(idx > d) & (idx <= end)]
        if hist.empty or fwd_px.empty:
            continue
        recs = []
        for t in prices.columns:
            c = hist[t].dropna()
            f = fwd_px[t].dropna()
            if len(c) < 260 or len(f) < 20:
                continue
            res = price_only_score(c, params.weights)
            if res is None:
                continue
            sc, dd = res
            fr = float(f.iloc[-1] / c.iloc[-1] - 1)
            recs.append({"Stichtag": d, "Ticker": t, "Score": sc, "Abstand Hoch": dd, "Folgerendite": fr,
                         "Kandidat": dd <= -params.cand_min_drawdown})
        if not recs:
            continue
        r = pd.DataFrame(recs)
        cands = r[r["Kandidat"]].sort_values("Score", ascending=False)
        top = cands.head(top_n)
        r["Top"] = r["Ticker"].isin(top["Ticker"])
        detail_rows.append(r)
        period_rows.append({
            "Stichtag": d, "Top-Auswahl": top["Folgerendite"].mean() if len(top) else np.nan,
            "Alle Kandidaten": cands["Folgerendite"].mean() if len(cands) else np.nan,
            "Gesamtes Universum": r["Folgerendite"].mean(),
            "Trefferquote Top": (top["Folgerendite"] > 0).mean() if len(top) else np.nan,
            "Anzahl Kandidaten": len(cands),
        })
    periods = pd.DataFrame(period_rows)
    details = pd.concat(detail_rows, ignore_index=True) if detail_rows else pd.DataFrame()
    return periods, details
