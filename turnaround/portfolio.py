"""Zusammenstellung eines Musterportfolios aus Turnaround-Kandidaten."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "portfolios"


def build_portfolio(scored: pd.DataFrame, n: int = 8, max_per_sector: int = 2, max_weight: float = 0.20,
                    min_score: float = 50.0, exclude_flags: tuple[str, ...] = ("Existenz-Nachrichten",),
                    weighting: str = "Score × Risiko") -> pd.DataFrame:
    """Wählt die besten Kandidaten mit Sektor-Diversifikation und gewichtet sie.

    weighting: "Gleichgewichtet", "Score" oder "Score × Risiko"
    (Score geteilt durch Volatilität -> riskantere Titel bekommen weniger Gewicht).
    """
    if scored.empty:
        return scored
    pool = scored[scored["Kandidat"] & (scored["Score"] >= min_score)].copy()
    for fl in exclude_flags:
        pool = pool[~pool["Risiken"].str.contains(fl, na=False)]
    pool = pool.sort_values("Score", ascending=False)
    picks, per_sector = [], {}
    for _, row in pool.iterrows():
        sec = row["Sektor"]
        if per_sector.get(sec, 0) >= max_per_sector:
            continue
        picks.append(row)
        per_sector[sec] = per_sector.get(sec, 0) + 1
        if len(picks) >= n:
            break
    if not picks:
        return pd.DataFrame()
    pf = pd.DataFrame(picks).reset_index(drop=True)
    if weighting == "Gleichgewichtet":
        raw = np.ones(len(pf))
    elif weighting == "Score":
        raw = pf["Score"].to_numpy(dtype=float)
    else:
        vol = pf["Vola 1J"].fillna(pf["Vola 1J"].median()).clip(lower=0.15).to_numpy(dtype=float)
        raw = pf["Score"].to_numpy(dtype=float) / vol
    pf["Gewicht"] = cap_weights(raw / raw.sum(), max_weight)
    return pf


def cap_weights(w: np.ndarray, cap: float) -> np.ndarray:
    """Begrenzt Einzelgewichte iterativ und verteilt Überschüsse proportional."""
    w = np.asarray(w, dtype=float).copy()
    if cap * len(w) < 1:  # Obergrenze nicht erreichbar -> gleichgewichtet
        return np.full(len(w), 1 / len(w))
    for _ in range(50):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        free = ~over & (w < cap)
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w / w.sum()


def allocate(pf: pd.DataFrame, amount: float) -> pd.DataFrame:
    out = pf.copy()
    out["Betrag"] = out["Gewicht"] * amount
    out["Stück (ca.)"] = np.floor(out["Betrag"] / out["Kurs"]).astype(int)
    return out


def backward_performance(prices: pd.DataFrame, pf: pd.DataFrame, months: int = 12,
                         weeks: int | None = None) -> pd.Series:
    """Rückblick: Wertentwicklung der heutigen Auswahl (kein echter Backtest).
    Mit ``weeks`` wird statt ``months`` ein Zeitraum in Wochen betrachtet."""
    offset = pd.DateOffset(weeks=weeks) if weeks else pd.DateOffset(months=months)
    start = prices.index.max() - offset
    px = prices.loc[prices.index >= start, pf["Ticker"]].ffill().dropna(how="all")
    px = px.bfill()
    norm = px / px.iloc[0]
    w = pf.set_index("Ticker")["Gewicht"]
    return (norm * w).sum(axis=1)


# --------------------------------------------------------------------------- #
# Snapshots zum Nachverfolgen
# --------------------------------------------------------------------------- #
def save_snapshot(pf: pd.DataFrame, label: str = "") -> Path:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    data = {
        "datum": today, "label": label,
        "positionen": [
            {"ticker": r.Ticker, "name": r.Name, "gewicht": float(r.Gewicht), "einstieg": float(r.Kurs),
             "score": float(r.Score), "waehrung": r.Währung}
            for r in pf.itertuples()
        ],
    }
    p = SNAPSHOT_DIR / f"portfolio_{today}_{len(list(SNAPSHOT_DIR.glob('*.json'))) + 1}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def list_snapshots() -> list[dict]:
    if not SNAPSHOT_DIR.exists():
        return []
    out = []
    for p in sorted(SNAPSHOT_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["_file"] = p.name
            out.append(d)
        except Exception:
            continue
    return out


def snapshot_performance(snap: dict, prices: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    rows = []
    for pos in snap["positionen"]:
        t = pos["ticker"]
        cur = prices[t].dropna().iloc[-1] if t in prices and prices[t].notna().any() else np.nan
        perf = cur / pos["einstieg"] - 1 if pos["einstieg"] else np.nan
        rows.append({"Ticker": t, "Name": pos["name"], "Gewicht": pos["gewicht"], "Einstieg": pos["einstieg"],
                     "Aktuell": cur, "Performance": perf})
    df = pd.DataFrame(rows)
    total = float((df["Gewicht"] * df["Performance"]).sum() / df.loc[df["Performance"].notna(), "Gewicht"].sum()) \
        if df["Performance"].notna().any() else np.nan
    return df, total
