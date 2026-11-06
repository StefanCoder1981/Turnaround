"""Plotly-Charts. Farben folgen festen Rollen (nie zyklisch vergeben)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# Kategoriale Slots in fester Reihenfolge + Statusfarben (reserviert)
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"
MUTED = "#898781"
GOOD, WARNING, CRITICAL = "#0ca30c", "#fab219", "#d03b3b"

BASE_LAYOUT = dict(
    margin=dict(l=10, r=10, t=40, b=10),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif'),
)


def _layout(fig: go.Figure, title: str = "", height: int = 380, **kw) -> go.Figure:
    fig.update_layout(title=dict(text=title, x=0, font=dict(size=15)), height=height, **{**BASE_LAYOUT, **kw})
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridwidth=1, zeroline=False)
    return fig


def price_chart(close: pd.Series, title: str, episode: dict | None = None, currency: str = "") -> go.Figure:
    s = close.dropna()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s.index, y=s, name="Kurs", line=dict(color=S1, width=2),
                             hovertemplate="%{y:,.2f} " + currency + "<extra>Kurs</extra>"))
    fig.add_trace(go.Scatter(x=s.index, y=s.rolling(50).mean(), name="50-Tage-Linie",
                             line=dict(color=S2, width=1.5), hovertemplate="%{y:,.2f}<extra>SMA50</extra>"))
    fig.add_trace(go.Scatter(x=s.index, y=s.rolling(200).mean(), name="200-Tage-Linie",
                             line=dict(color=S3, width=1.5), hovertemplate="%{y:,.2f}<extra>SMA200</extra>"))
    if episode:
        pts = []
        for key_d, key_v, label in (("Hist. Hoch-Datum", None, "Hoch"), ("Hist. Tief-Datum", None, "Tief"),
                                    ("Hist. Erholungs-Hoch-Datum", None, "Erholung")):
            d = episode.get(key_d)
            if d is not None and not pd.isna(d) and d in s.index:
                pts.append((d, float(s.loc[d]), label))
        if pts:
            fig.add_trace(go.Scatter(
                x=[p[0] for p in pts], y=[p[1] for p in pts], mode="markers+text", name="Wendepunkte",
                text=[p[2] for p in pts], textposition="top center",
                marker=dict(size=11, color=S1, line=dict(width=2, color="white")),
                hovertemplate="%{text}: %{y:,.2f}<extra></extra>"))
    return _layout(fig, title, height=420, yaxis_title=currency or None)


def factor_bars(row: pd.Series, factors: list[str]) -> go.Figure:
    vals = [row.get(f"S_{f}") for f in factors]
    vals = [np.nan if v is None else v * 100 for v in vals]
    fig = go.Figure(go.Bar(
        x=vals, y=factors, orientation="h", marker=dict(color=S1, cornerradius=4),
        text=[("–" if np.isnan(v) else f"{v:.0f}") for v in vals], textposition="outside",
        hovertemplate="%{y}: %{x:.0f} / 100<extra></extra>"))
    fig.update_xaxes(range=[0, 110], title="Teil-Score (0–100)")
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, "Score-Zusammensetzung", height=300, hovermode="closest", showlegend=False)


def fundamentals_chart(hist: pd.DataFrame, currency: str = "") -> go.Figure | None:
    if hist is None or hist.empty:
        return None
    fig = go.Figure()
    cols = [("Umsatz", S1), ("Operatives Ergebnis", S2), ("Nettogewinn", S3)]
    for col, color in cols:
        if col in hist and hist[col].notna().any():
            fig.add_trace(go.Bar(x=hist.index.astype(str), y=hist[col] / 1e6, name=col,
                                 marker=dict(color=color, cornerradius=4),
                                 hovertemplate="%{y:,.0f} Mio. " + currency + "<extra>" + col + "</extra>"))
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
    return _layout(fig, "Geschäftsentwicklung (Jahreswerte)", height=340, yaxis_title=f"Mio. {currency}")


def margin_chart(hist: pd.DataFrame) -> go.Figure | None:
    if hist is None or hist.empty or "Operative Marge" not in hist or not hist["Operative Marge"].notna().any():
        return None
    m = hist["Operative Marge"] * 100
    fig = go.Figure(go.Scatter(x=hist.index.astype(str), y=m, mode="lines+markers+text", name="Operative Marge",
                               line=dict(color=S1, width=2), marker=dict(size=9),
                               text=[f"{v:.1f} %" if not np.isnan(v) else "" for v in m],
                               textposition="top center", hovertemplate="%{y:.1f} %<extra></extra>"))
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    return _layout(fig, "Operative Marge", height=340, yaxis_title="%", showlegend=False)


def overview_scatter(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for flag, color, name in ((False, MUTED, "Kein Kandidat"), (True, S1, "Turnaround-Kandidat")):
        d = df[df["Kandidat"] == flag]
        fig.add_trace(go.Scatter(
            x=d["Abstand Hoch"] * 100, y=d["Score"], mode="markers", name=name,
            marker=dict(size=10, color=color, line=dict(width=1.5, color="white"), opacity=0.9),
            customdata=np.stack([d["Name"], d["Ticker"], d["Phase"]], axis=-1) if len(d) else None,
            hovertemplate="<b>%{customdata[0]}</b> (%{customdata[1]})<br>Abstand zum Hoch: %{x:.0f} %"
                          "<br>Score: %{y:.0f}<br>Phase: %{customdata[2]}<extra></extra>"))
    fig.update_xaxes(title="Abstand zum Hoch im Analysezeitraum (%)")
    fig.update_yaxes(title="Turnaround-Score")
    return _layout(fig, "Alle Titel: Abschlag vs. Score", height=420, hovermode="closest")


def phase_bars(df: pd.DataFrame) -> go.Figure:
    order = ["Abwärtstrend", "Bodenbildung", "Frühe Erholung", "Turnaround läuft", "Seitwärts / Korrektur", "Nahe Hoch"]
    counts = df["Phase"].value_counts().reindex(order).fillna(0)
    fig = go.Figure(go.Bar(x=counts.values, y=counts.index, orientation="h", marker=dict(color=S1, cornerradius=4),
                           text=counts.values.astype(int), textposition="outside",
                           hovertemplate="%{y}: %{x:.0f} Titel<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, "Titel nach Turnaround-Phase", height=320, hovermode="closest", showlegend=False)


def hist_scatter(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for flag, color, name in ((False, S2, "Ohne Fundamental-Bestätigung"), (True, S1, "Fundamental bestätigt")):
        d = df[df["Fundamental bestätigt"] == flag]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["Hist. Einbruch"] * 100, y=d["Hist. Erholung"] * 100, mode="markers", name=name,
            marker=dict(size=11, color=color, line=dict(width=1.5, color="white")),
            customdata=np.stack([d["Name"], d["Ticker"], d["Hist. Status"]], axis=-1),
            hovertemplate="<b>%{customdata[0]}</b> (%{customdata[1]})<br>Einbruch: %{x:.0f} %"
                          "<br>Erholung vom Tief: +%{y:.0f} %<br>%{customdata[2]}<extra></extra>"))
    fig.update_xaxes(title="Einbruch vom Hoch (%)")
    fig.update_yaxes(title="Erholung vom Tief (%)")
    return _layout(fig, "Historische Turnarounds: Einbruch vs. Erholung", height=420, hovermode="closest")


def weight_bars(pf: pd.DataFrame) -> go.Figure:
    d = pf.sort_values("Gewicht")
    fig = go.Figure(go.Bar(x=d["Gewicht"] * 100, y=d["Name"], orientation="h", marker=dict(color=S1, cornerradius=4),
                           text=[f"{w:.1f} %" for w in d["Gewicht"] * 100], textposition="outside",
                           customdata=d["Score"], hovertemplate="%{y}: %{x:.1f} %<br>Score %{customdata:.0f}<extra></extra>"))
    fig.update_xaxes(title="Gewicht (%)", range=[0, max(d["Gewicht"]) * 100 * 1.25])
    return _layout(fig, "Gewichtung", height=max(260, 40 * len(d) + 80), hovermode="closest", showlegend=False)


def sector_bars(pf: pd.DataFrame) -> go.Figure:
    s = pf.groupby("Sektor")["Gewicht"].sum().sort_values()
    fig = go.Figure(go.Bar(x=s.values * 100, y=s.index, orientation="h", marker=dict(color=S1, cornerradius=4),
                           text=[f"{v:.0f} %" for v in s.values * 100], textposition="outside",
                           hovertemplate="%{y}: %{x:.1f} %<extra></extra>"))
    fig.update_xaxes(title="Gewicht (%)", range=[0, s.max() * 100 * 1.3])
    return _layout(fig, "Sektoren", height=max(220, 40 * len(s) + 80), hovermode="closest", showlegend=False)


def performance_lines(series: dict[str, pd.Series], title: str) -> go.Figure:
    colors = [S1, MUTED, S2]
    fig = go.Figure()
    for (name, s), c in zip(series.items(), colors):
        fig.add_trace(go.Scatter(x=s.index, y=(s - 1) * 100, name=name, line=dict(color=c, width=2),
                                 hovertemplate="%{y:+.1f} %<extra>" + name + "</extra>"))
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    return _layout(fig, title, height=360, yaxis_title="Wertentwicklung (%)")


def backtest_lines(periods: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col, color in (("Top-Auswahl", S1), ("Alle Kandidaten", S2), ("Gesamtes Universum", MUTED)):
        fig.add_trace(go.Scatter(x=periods["Stichtag"], y=periods[col] * 100, name=col, mode="lines+markers",
                                 line=dict(color=color, width=2), marker=dict(size=8),
                                 hovertemplate="%{y:+.1f} %<extra>" + col + "</extra>"))
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    return _layout(fig, "Durchschnittliche Folgerendite je Stichtag", height=380, yaxis_title="Folgerendite (%)")


def sentiment_bars(news: pd.DataFrame) -> go.Figure | None:
    if news is None or news.empty:
        return None
    d = news.dropna(subset=["Datum"]).copy()
    if d.empty:
        return None
    d["Woche"] = pd.to_datetime(d["Datum"]).dt.tz_localize(None).dt.to_period("W").dt.start_time
    g = d.groupby("Woche")["Stimmung"].agg(["sum", "count"]).reset_index()
    colors = [GOOD if v > 0 else CRITICAL if v < 0 else MUTED for v in g["sum"]]
    fig = go.Figure(go.Bar(x=g["Woche"], y=g["sum"], marker=dict(color=colors, cornerradius=4),
                           customdata=g["count"],
                           hovertemplate="Woche ab %{x|%d.%m.%Y}<br>Stimmungssumme: %{y:+.1f}"
                                         "<br>%{customdata} Meldungen<extra></extra>"))
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    return _layout(fig, "Nachrichtenstimmung pro Woche (▲ positiv / ▼ negativ)", height=300,
                   hovermode="closest", showlegend=False)
