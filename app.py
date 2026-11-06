"""Turnaround-Scanner – Streamlit-Oberfläche.

Start:  streamlit run app.py   (oder start.bat)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from turnaround import analysis, brokers, charts, data, news, portfolio
from turnaround.brokers import rebalance
from turnaround.universe import DEFAULT_UNIVERSES, UNIVERSES, build_universe

st.set_page_config(page_title="Turnaround-Scanner", page_icon="🔄", layout="wide")

FACTORS = list(analysis.FACTOR_HELP)
PHASES = ["Abwärtstrend", "Bodenbildung", "Frühe Erholung", "Turnaround läuft", "Seitwärts / Korrektur", "Nahe Hoch"]


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #
def pct(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce") * 100
    return out


def fmt_pct(v, signed=True) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    return (f"{v * 100:+.1f} %" if signed else f"{v * 100:.1f} %").replace(".", ",")


def fmt_big(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    for div, suf in ((1e12, "Bio."), (1e9, "Mrd."), (1e6, "Mio.")):
        if abs(v) >= div:
            return f"{de_num(v / div, 1)} {suf}"
    return de_num(v, 0)


def de_num(v, decimals: int = 2) -> str:
    """Zahl im deutschen Format (1.234,56)."""
    return f"{v:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def label(row) -> str:
    return f"{row['Name']} ({row['Ticker']})"


def progress_cb(bar):
    return lambda frac, text: bar.progress(min(max(frac, 0.0), 1.0), text=text)


def score_col(title="Score"):
    return st.column_config.ProgressColumn(title, min_value=0, max_value=100, format="%.0f")


PCT = lambda title: st.column_config.NumberColumn(title, format="%.1f %%")  # noqa: E731


def reasoning(row: pd.Series, news_df: pd.DataFrame | None) -> str:
    """Kurze, regelbasierte Begründung für eine Portfolio-Position."""
    sub = {f: row.get(f"S_{f}") for f in FACTORS}
    strong = sorted([(k, v) for k, v in sub.items() if v is not None and not np.isnan(v)], key=lambda x: -x[1])[:2]
    parts = [f"{row['Phase']}, {fmt_pct(row['Abstand Hoch'])} unter Hoch, {fmt_pct(row['Erholung vom Tief'])} vom Tief"]
    if strong:
        parts.append("Stärken: " + ", ".join(f"{k} ({v * 100:.0f})" for k, v in strong))
    if row.get("Gewinnwende"):
        parts.append("Rückkehr in die Gewinnzone")
    mt = row.get("Margentrend")
    if mt is not None and not np.isnan(mt) and abs(mt) > 0.01:
        parts.append(f"Marge {mt * 100:+.1f} Pp. ggü. Vorjahr")
    if news_df is not None and not news_df.empty:
        best = news_df[news_df["Stimmung"] > 0].head(1)
        if len(best):
            parts.append(f"News: „{best.iloc[0]['Titel'][:90]}“")
    if row.get("Risiken"):
        parts.append(f"⚠️ {row['Risiken']}")
    return " · ".join(parts)


def news_alignment(row: pd.Series) -> str:
    sig = np.nanmean([row.get("S_Momentum"), row.get("S_Bodenbildung"), row.get("S_Fundamental")])
    nw = row.get("S_News")
    if nw is None or np.isnan(nw):
        return "➖ Keine News"
    if sig >= 0.55 and nw >= 0.55:
        return "✅ Bestätigt"
    if sig >= 0.55 and nw < 0.45:
        return "⚠️ News widersprechen"
    if sig < 0.45 and nw >= 0.55:
        return "💡 News vorauslaufend"
    if sig < 0.45 and nw < 0.45:
        return "🔻 Beides schwach"
    return "➖ Neutral"


# --------------------------------------------------------------------------- #
# Darstellung (Hell / Dunkel / System)
# --------------------------------------------------------------------------- #
# Streamlit übernimmt ein Theme nur beim Laden der Seite aus dem Browser-Speicher
# (Schlüssel wie im Streamlit-Frontend). Die Wahl steht zusätzlich im URL-Parameter
# ?theme=…, damit sie nach dem Neuladen erhalten bleibt und die Charts sie kennen.
THEME_OPTIONS = {"system": "💻 System", "light": "☀️ Hell", "dark": "🌙 Dunkel"}
_ST_THEME_NAME = {"system": "System", "light": "Light", "dark": "Dark"}

theme_pref = st.query_params.get("theme", "system")
if theme_pref not in THEME_OPTIONS:
    theme_pref = "system"
st.session_state.setdefault("theme_choice", theme_pref)


def _on_theme_change() -> None:
    st.session_state["theme_pending"] = st.session_state["theme_choice"]


pending = st.session_state.pop("theme_pending", None)
if pending and pending != theme_pref:
    # Eigene, benannte Container: Streamlit führt ein Skript nur beim Einhängen eines neuen Elements aus.
    st.container(key="theme_switch").html(f"""<script>(() => {{
        const key = `stActiveTheme-${{window.location.pathname}}-v2`;
        window.localStorage.setItem(key, JSON.stringify("{_ST_THEME_NAME[pending]}"));
        const url = new URL(window.location.href);
        url.searchParams.set("theme", "{pending}");
        window.location.replace(url.toString());
    }})();</script>""", unsafe_allow_javascript=True)
    st.info("Darstellung wird umgeschaltet …")
    st.stop()

if theme_pref != "system":
    # URL (?theme=dark, z. B. aus einem Lesezeichen) und Browser-Speicher abgleichen.
    # Lädt nur neu, wenn sie abweichen – danach stimmen sie überein, also keine Schleife.
    st.container(key="theme_sync").html(f"""<script>(() => {{
        const key = `stActiveTheme-${{window.location.pathname}}-v2`;
        const want = JSON.stringify("{_ST_THEME_NAME[theme_pref]}");
        if (window.localStorage.getItem(key) !== want) {{
            window.localStorage.setItem(key, want);
            window.location.reload();
        }}
    }})();</script>""", unsafe_allow_javascript=True)

ctx_theme = getattr(st.context.theme, "type", None) if hasattr(st.context, "theme") else None
charts.set_mode(theme_pref if theme_pref != "system" else (ctx_theme or "light"))

# --------------------------------------------------------------------------- #
# Seitenleiste
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("🔄 Turnaround-Scanner")
    st.radio("Darstellung", list(THEME_OPTIONS), format_func=THEME_OPTIONS.get, key="theme_choice",
             horizontal=True, on_change=_on_theme_change)
    sel =st.multiselect("Aktien-Universum", list(UNIVERSES), default=DEFAULT_UNIVERSES)
    custom = st.text_input("Eigene Ticker (kommagetrennt, Yahoo-Format)", placeholder="z. B. LHA.DE, INTC, RR.L")
    tickers = build_universe(sel, custom)
    st.caption(f"{len(tickers)} Titel ausgewählt")

    with st.expander("Analyse-Zeitraum & Kriterien", expanded=False):
        lookback = st.slider("Analysezeitraum (Jahre)", 3, 5, 5)
        hist_dd = st.slider("Hist. Turnaround: Mindest-Einbruch", 20, 80, 40, 5, format="%d %%") / 100
        hist_rec = st.slider("Hist. Turnaround: Mindest-Erholung vom Tief", 30, 300, 60, 10, format="%d %%") / 100
        cand_dd = st.slider("Kandidat: min. Abstand zum Hoch", 15, 70, 30, 5, format="%d %%") / 100
        min_cap = st.number_input("Min. Marktkapitalisierung (Mio.)", 0, 50_000, 300, 100) * 1e6

    with st.expander("Gewichtung der Faktoren", expanded=False):
        defaults = analysis.Params().weights
        weights = {f: st.slider(f, 0.0, 3.0, float(defaults[f]), 0.25, help=analysis.FACTOR_HELP[f]) for f in FACTORS}

    with st.expander("Nachrichten", expanded=False):
        news_all = st.checkbox("News für alle Titel abrufen (langsamer)", value=False)
        news_top = st.slider("Sonst: News für die besten N Kandidaten", 5, 80, 30, 5)

    with st.expander("Musterportfolio", expanded=True):
        pf_n = st.slider("Anzahl Positionen", 3, 20, 8)
        pf_sector = st.slider("Max. Titel je Sektor", 1, 5, 2)
        pf_maxw = st.slider("Max. Einzelgewicht", 5, 50, 20, 5, format="%d %%") / 100
        pf_minscore = st.slider("Mindest-Score", 0, 90, 50, 5)
        pf_weighting = st.selectbox("Gewichtung", ["Score × Risiko", "Score", "Gleichgewichtet"])
        pf_amount = st.number_input("Anlagebetrag", 1_000, 10_000_000, 10_000, 1_000)

    refresh = st.button("🔁 Daten neu laden", width="stretch",
                        help="Löscht den Cache und lädt Kurse, Fundamentaldaten und News neu.")

params = analysis.Params(lookback_years=lookback, hist_min_drawdown=hist_dd, hist_min_recovery=hist_rec,
                         cand_min_drawdown=cand_dd, min_market_cap=min_cap, weights=weights)

# --------------------------------------------------------------------------- #
# Daten laden
# --------------------------------------------------------------------------- #
st.title("Turnaround-Scanner")
st.caption("Findet Turnarounds der letzten Jahre, gleicht Kurs-, Fundamental- und Nachrichtendaten ab und schlägt "
           "ein Musterportfolio möglicher künftiger Turnaround-Kandidaten vor. **Keine Anlageberatung.**")

if not tickers:
    st.info("Bitte links mindestens ein Universum oder eigene Ticker wählen.")
    st.stop()

if refresh:
    data.clear_cache()
    for k in ("loaded_key", "prices", "funds", "news"):
        st.session_state.pop(k, None)

key = tuple(tickers)
if st.session_state.get("loaded_key") != key:
    box = st.empty()
    with box.container():
        bar = st.progress(0.0, text="Starte …")
        prices = data.load_prices(tickers, years=6, progress=progress_cb(bar))
        if prices.empty:
            st.error("Es konnten keine Kursdaten geladen werden. Internetverbindung prüfen.")
            st.stop()
        funds = data.load_fundamentals(list(prices.columns), progress=progress_cb(bar))
    box.empty()
    st.session_state.update(loaded_key=key, prices=prices, funds=funds)
    st.session_state.setdefault("news", {})

prices: pd.DataFrame = st.session_state["prices"]
funds: dict = st.session_state["funds"]
news_store: dict = st.session_state.setdefault("news", {})
missing_tickers = [t for t in tickers if t not in prices.columns]

# Vorläufiges Scoring -> News für die relevanten Titel nachladen -> finales Scoring
pre = analysis.analyze_universe(prices, funds, {}, params)
if pre.empty:
    st.error("Zu wenig Kurshistorie für die Analyse.")
    st.stop()
need = list(pre["Ticker"]) if news_all else list(pre[pre["Kandidat"]].head(news_top)["Ticker"])
to_fetch = [t for t in need if t not in news_store]
if to_fetch:
    names = pre.set_index("Ticker")["Name"].to_dict()
    box = st.empty()
    with box.container():
        bar = st.progress(0.0, text="Lade Nachrichten …")
        news_store.update(news.load_news_bulk([(t, names.get(t)) for t in to_fetch], progress=progress_cb(bar)))
    box.empty()
news_summary = {t: news.summarize_news(df) for t, df in news_store.items()}
scored = analysis.analyze_universe(prices, funds, news_summary, params)
scored["News-Abgleich"] = scored.apply(news_alignment, axis=1)
cands = scored[scored["Kandidat"]]
hist_ta = scored[scored["Hist. Turnaround"] | (scored["Hist. Status"] == "Laufend")]
pf = portfolio.build_portfolio(scored, n=pf_n, max_per_sector=pf_sector, max_weight=pf_maxw,
                               min_score=pf_minscore, weighting=pf_weighting)
name_of = scored.set_index("Ticker")["Name"].to_dict()

# --------------------------------------------------------------------------- #
# Kopfzeile
# --------------------------------------------------------------------------- #
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Analysierte Titel", len(scored), help=f"{len(missing_tickers)} Ticker ohne Daten" if missing_tickers else None)
c2.metric("Hist. Turnarounds", int(scored["Hist. Turnaround"].sum()),
          help=f"Einbruch ≥ {hist_dd:.0%} und Erholung ≥ {hist_rec:.0%} in {lookback} Jahren")
c3.metric("Laufende Turnarounds", int((scored["Hist. Status"] == "Laufend").sum()))
c4.metric("Aktuelle Kandidaten", len(cands), help=f"Mind. {cand_dd:.0%} unter dem Hoch")
c5.metric("Ø Score Portfolio", f"{pf['Score'].mean():.0f}" if not pf.empty else "–")

tabs = st.tabs(["📊 Übersicht", "🔄 Historische Turnarounds", "🎯 Kandidaten", "📰 News-Abgleich",
                "💼 Musterportfolio", "🔍 Einzelanalyse", "🧪 Signal-Check", "🏦 Depots", "ℹ️ Methodik"])

# --------------------------------------------------------------------------- #
# Übersicht
# --------------------------------------------------------------------------- #
with tabs[0]:
    a, b = st.columns([3, 2])
    a.plotly_chart(charts.overview_scatter(scored), width="stretch")
    b.plotly_chart(charts.phase_bars(scored), width="stretch")
    st.subheader("Top 10 Turnaround-Kandidaten")
    top = pct(cands.head(10), ["Abstand Hoch", "Erholung vom Tief", "Rendite 3M"])
    st.dataframe(top[["Name", "Ticker", "Sektor", "Phase", "Score", "Abstand Hoch", "Erholung vom Tief",
                      "Rendite 3M", "News-Abgleich", "Risiken"]],
                 hide_index=True, width="stretch",
                 column_config={"Score": score_col(), "Abstand Hoch": PCT("Abstand Hoch"),
                                "Erholung vom Tief": PCT("Vom Tief"), "Rendite 3M": PCT("3 Monate")})
    if missing_tickers:
        st.caption("Ohne Kursdaten (evtl. delistet/falscher Ticker): " + ", ".join(missing_tickers))

# --------------------------------------------------------------------------- #
# Historische Turnarounds
# --------------------------------------------------------------------------- #
with tabs[1]:
    st.markdown(f"Titel, die in den letzten **{lookback} Jahren** mindestens **{hist_dd:.0%}** eingebrochen sind "
                f"und sich danach um mindestens **{hist_rec:.0%}** erholt haben (bzw. *laufend*: ≥ 25 % Erholung).")
    if hist_ta.empty:
        st.info("Keine historischen Turnarounds mit den gewählten Kriterien gefunden.")
    else:
        status_f = st.multiselect("Status", sorted(hist_ta["Hist. Status"].dropna().unique()),
                                  default=sorted(hist_ta["Hist. Status"].dropna().unique()))
        h = hist_ta[hist_ta["Hist. Status"].isin(status_f)].sort_values("Hist. Erholung", ascending=False)
        a, b = st.columns([2, 3])
        a.plotly_chart(charts.hist_scatter(h), width="stretch")
        hv = pct(h, ["Hist. Einbruch", "Hist. Erholung", "Abstand Hoch"])
        b.dataframe(hv[["Name", "Ticker", "Hist. Status", "Hist. Einbruch", "Hist. Erholung", "Hist. Tief-Datum",
                        "Hist. Erholungsphase (Tage)", "Fundamental bestätigt", "Abstand Hoch"]],
                    hide_index=True, width="stretch", height=420,
                    column_config={"Hist. Status": "Status", "Hist. Einbruch": PCT("Einbruch"),
                                   "Hist. Erholung": PCT("Erholung"),
                                   "Hist. Tief-Datum": st.column_config.DateColumn("Tief am", format="DD.MM.YYYY"),
                                   "Hist. Erholungsphase (Tage)": "Erholung (Tage)",
                                   "Fundamental bestätigt": "Fundamental ✓",
                                   "Abstand Hoch": PCT("Heute vs. Hoch")})

        st.subheader("Fallstudie: Was geschah am Wendepunkt?")
        opts = h["Ticker"].tolist()
        pick = st.selectbox("Titel", opts, format_func=lambda t: f"{name_of.get(t, t)} ({t})", key="hist_pick")
        row = h.set_index("Ticker").loc[pick]
        close = prices[pick].dropna()
        close = close[close.index >= close.index.max() - pd.DateOffset(years=lookback)]
        st.plotly_chart(charts.price_chart(close, f"{row['Name']}: Einbruch und Erholung", row.to_dict(),
                                           row["Währung"]), width="stretch")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Einbruch", fmt_pct(row["Hist. Einbruch"]), f"{row['Hist. Abwärtsphase (Tage)']} Tage", delta_color="off")
        m2.metric("Erholung vom Tief", fmt_pct(row["Hist. Erholung"]), f"{row['Hist. Erholungsphase (Tage)']} Tage",
                  delta_color="off")
        m3.metric("Verlust aufgeholt", fmt_pct(row["Hist. Anteil aufgeholt"], signed=False))
        m4.metric("Tiefpunkt", pd.Timestamp(row["Hist. Tief-Datum"]).strftime("%d.%m.%Y"))
        if st.button("📰 Nachrichten rund um den Tiefpunkt laden (± 45 Tage)", key="news_trough"):
            with st.spinner("Suche historische Nachrichten …"):
                st.session_state[f"trough_news_{pick}"] = news.news_around(pick, row["Name"],
                                                                         pd.Timestamp(row["Hist. Tief-Datum"]))
        tn = st.session_state.get(f"trough_news_{pick}")
        if tn is not None:
            if tn.empty:
                st.info("Keine historischen Nachrichten gefunden.")
            else:
                cats = tn["Kategorien"].str.split(", ").explode()
                cats = cats[cats.astype(bool)].value_counts()
                if len(cats):
                    st.markdown("**Themen am Wendepunkt:** " + " · ".join(f"{k} ({v})" for k, v in cats.items()))
                st.dataframe(tn[["Datum", "Titel", "Quelle", "Stimmung", "Kategorien", "Link"]], hide_index=True,
                             width="stretch",
                             column_config={"Datum": st.column_config.DatetimeColumn(format="DD.MM.YYYY"),
                                            "Link": st.column_config.LinkColumn(display_text="öffnen"),
                                            "Stimmung": st.column_config.NumberColumn(format="%+.1f")})

# --------------------------------------------------------------------------- #
# Kandidaten
# --------------------------------------------------------------------------- #
with tabs[2]:
    st.markdown(f"Aktien mit mindestens **{cand_dd:.0%}** Abstand zum Hoch, bewertet nach sechs Faktoren "
                "(Gewichtung links anpassbar). Risiko-Flags verringern den Score.")
    f1, f2, f3 = st.columns(3)
    ph = f1.multiselect("Phase", PHASES, default=[p for p in PHASES if p not in ("Nahe Hoch",)])
    secs = sorted(cands["Sektor"].unique())
    sf = f2.multiselect("Sektor", secs, default=secs)
    hide_flags = f3.checkbox("Titel mit Risiko-Flags ausblenden", value=False)
    c = cands[cands["Phase"].isin(ph) & cands["Sektor"].isin(sf)]
    if hide_flags:
        c = c[c["Risiken"] == ""]
    cv = pct(c, ["Abstand Hoch", "Erholung vom Tief", "Rendite 3M", "Rendite 12M", "Margentrend", "Umsatzwachstum"]
             + [f"S_{f}" for f in FACTORS])
    cols = ["Name", "Ticker", "Sektor", "Phase", "Score", *[f"S_{f}" for f in FACTORS], "Abstand Hoch",
            "Erholung vom Tief", "Rendite 3M", "Margentrend", "Umsatzwachstum", "Gewinnwende", "KBV", "News-Abgleich",
            "Risiken"]
    cfg = {"Score": score_col(), "Abstand Hoch": PCT("Abstand Hoch"), "Erholung vom Tief": PCT("Vom Tief"),
           "Rendite 3M": PCT("3 Mon."), "Margentrend": PCT("Marge Δ Pp."), "Umsatzwachstum": PCT("Umsatz Δ"),
           "KBV": st.column_config.NumberColumn(format="%.2f")}
    cfg.update({f"S_{f}": st.column_config.NumberColumn(f, format="%.0f", help=analysis.FACTOR_HELP[f])
                for f in FACTORS})
    st.dataframe(cv[cols], hide_index=True, width="stretch", height=560, column_config=cfg)
    st.download_button("⬇️ Als CSV exportieren", c.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                       "turnaround_kandidaten.csv", "text/csv")

# --------------------------------------------------------------------------- #
# News-Abgleich
# --------------------------------------------------------------------------- #
with tabs[3]:
    st.markdown("Abgleich der Kurs- und Fundamentalsignale mit aktuellen Unternehmens- und Börsennachrichten "
                "(Yahoo Finance + Google News, DE/EN). Stimmung zeitgewichtet, Halbwertszeit 21 Tage.")
    with_news = scored[scored["News-Anzahl"] > 0]
    if with_news.empty:
        st.info("Noch keine Nachrichten geladen.")
    else:
        counts = with_news["News-Abgleich"].value_counts()
        cols_m = st.columns(len(counts))
        for col, (k, v) in zip(cols_m, counts.items()):
            col.metric(k, int(v))
        nv = with_news.copy()
        nv["News-Score"] = nv["S_News"] * 100
        nv["Signal-Score"] = nv[["S_Momentum", "S_Bodenbildung", "S_Fundamental"]].mean(axis=1) * 100
        st.dataframe(nv[["Name", "Ticker", "News-Abgleich", "Signal-Score", "News-Score", "News-Anzahl",
                         "Turnaround-Stichworte", "Risiken", "Score"]],
                     hide_index=True, width="stretch", height=360,
                     column_config={"Score": score_col("Gesamt-Score"), "News-Score": score_col("News"),
                                    "Signal-Score": score_col("Kurs+Fundamental")})

        st.subheader("Nachrichten je Titel")
        opts = with_news["Ticker"].tolist()
        pick = st.selectbox("Titel", opts, format_func=lambda t: f"{name_of.get(t, t)} ({t})", key="news_pick")
        ndf = news_store.get(pick, pd.DataFrame())
        nsum = news_summary.get(pick, {})
        if nsum.get("news_red_flags"):
            st.error("🚨 Existenzbezogene Meldungen: " + " | ".join(nsum["news_red_flags"]))
        a, b = st.columns([3, 2])
        fig = charts.sentiment_bars(ndf)
        if fig:
            a.plotly_chart(fig, width="stretch")
        cats = nsum.get("news_categories", {})
        if cats:
            b.markdown("**Themen der letzten 120 Tage**")
            b.dataframe(pd.DataFrame({"Thema": list(cats), "Meldungen": list(cats.values())}), hide_index=True,
                        width="stretch")
        if not ndf.empty:
            st.dataframe(ndf[["Datum", "Titel", "Quelle", "Stimmung", "Kategorien", "Treffer", "Link"]],
                         hide_index=True, width="stretch",
                         column_config={"Datum": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
                                        "Stimmung": st.column_config.NumberColumn(format="%+.1f"),
                                        "Link": st.column_config.LinkColumn(display_text="öffnen")})

# --------------------------------------------------------------------------- #
# Musterportfolio
# --------------------------------------------------------------------------- #
with tabs[4]:
    if pf.empty:
        st.warning("Kein Titel erfüllt die Portfolio-Kriterien – Mindest-Score senken oder Universum erweitern.")
    else:
        st.markdown(f"**{len(pf)} Positionen** aus {len(cands)} Kandidaten · max. {pf_sector} je Sektor · "
                    f"max. {pf_maxw:.0%} je Titel · Gewichtung: {pf_weighting}")
        alloc = portfolio.allocate(pf, pf_amount)
        alloc["Begründung"] = [reasoning(r, news_store.get(r["Ticker"])) for _, r in alloc.iterrows()]
        av = pct(alloc, ["Gewicht", "Abstand Hoch"])
        st.dataframe(av[["Name", "Ticker", "Sektor", "Phase", "Score", "Gewicht", "Betrag", "Kurs", "Währung",
                         "Stück (ca.)", "Abstand Hoch", "Begründung"]],
                     hide_index=True, width="stretch",
                     column_config={"Score": score_col(), "Gewicht": PCT("Gewicht"),
                                    "Betrag": st.column_config.NumberColumn(format="%.0f"),
                                    "Kurs": st.column_config.NumberColumn(format="%.2f"),
                                    "Abstand Hoch": PCT("vs. Hoch"),
                                    "Begründung": st.column_config.TextColumn(width="large")})
        if alloc["Währung"].nunique() > 1:
            st.caption("Hinweis: Positionen notieren in verschiedenen Währungen; Stückzahlen sind auf die "
                       "Handelswährung bezogen und ohne Wechselkursumrechnung nur grob.")
        a, b = st.columns([3, 2])
        a.plotly_chart(charts.weight_bars(pf), width="stretch")
        b.plotly_chart(charts.sector_bars(pf), width="stretch")

        # Zeitraum: (Wochen, Monate) – genau einer von beiden ist gesetzt
        periods = {"1 Woche": (1, None), "3 Monate": (None, 3), "6 Monate": (None, 6), "12 Monate": (None, 12)}
        period = st.radio("Rückblick", list(periods), index=3, horizontal=True)
        weeks, months = periods[period]
        pf_perf = portfolio.backward_performance(prices, pf, months or 12, weeks=weeks)
        uni = pd.DataFrame({"Ticker": list(prices.columns), "Gewicht": 1 / prices.shape[1]})
        uni_perf = portfolio.backward_performance(prices, uni, months or 12, weeks=weeks)
        st.plotly_chart(charts.performance_lines({"Musterportfolio": pf_perf, "Universum (gleichgewichtet)": uni_perf},
                                                 f"Rückblick: heutige Auswahl über {period}"),
                        width="stretch")
        st.caption("⚠️ Rückblick mit heutiger Auswahl = Rückschaufehler, kein Backtest. "
                   "Eine ehrliche Prüfung der Kurssignale liefert der Tab „Signal-Check“.")

        st.divider()
        s1, s2 = st.columns([3, 1])
        snap_label = s1.text_input("Bezeichnung für Snapshot", placeholder="z. B. Start Q4/2026")
        if s2.button("💾 Portfolio speichern", width="stretch"):
            p = portfolio.save_snapshot(pf, snap_label)
            st.success(f"Gespeichert: {p.name}")
        st.download_button("⬇️ Portfolio als CSV", alloc.drop(columns=[c for c in alloc if c.startswith("S_")])
                           .to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "musterportfolio.csv", "text/csv")

    snaps = portfolio.list_snapshots()
    if snaps:
        st.subheader("Gespeicherte Portfolios – Entwicklung seit Speicherung")
        all_t = sorted({p["ticker"] for s in snaps for p in s["positionen"]})
        snap_px = data.load_prices([t for t in all_t if t not in prices.columns]) if any(
            t not in prices.columns for t in all_t) else pd.DataFrame()
        px_all = prices.join(snap_px, how="outer") if not snap_px.empty else prices
        for s in snaps:
            df_s, total = portfolio.snapshot_performance(s, px_all)
            with st.expander(f"{s['datum']} {s.get('label') or ''} — Gesamt: {fmt_pct(total)}"):
                st.dataframe(pct(df_s, ["Gewicht", "Performance"]), hide_index=True, width="stretch",
                             column_config={"Gewicht": PCT("Gewicht"), "Performance": PCT("Performance"),
                                            "Einstieg": st.column_config.NumberColumn(format="%.2f"),
                                            "Aktuell": st.column_config.NumberColumn(format="%.2f")})

# --------------------------------------------------------------------------- #
# Einzelanalyse
# --------------------------------------------------------------------------- #
with tabs[5]:
    opts = scored["Ticker"].tolist()
    pick = st.selectbox("Titel wählen", opts, format_func=lambda t: f"{name_of.get(t, t)} ({t})", key="single_pick")
    row = scored.set_index("Ticker").loc[pick]
    f = funds.get(pick, {})
    info = f.get("info") or {}
    st.subheader(f"{row['Name']} ({pick})")
    st.caption(" · ".join(x for x in [f"**Phase: {row['Phase']}**", row["Sektor"], row["Branche"],
                                      info.get("country") or ""] if x))
    m = st.columns(5)
    m[0].metric("Score", f"{row['Score']:.0f}" if not np.isnan(row["Score"]) else "–",
                f"roh {row['Score roh']:.0f}" if row["Risiken"] else None, delta_color="off")
    m[1].metric("Kurs", f"{de_num(row['Kurs'])} {row['Währung']}")
    m[2].metric("Abstand Hoch", fmt_pct(row["Abstand Hoch"]))
    m[3].metric("Vom Tief", fmt_pct(row["Erholung vom Tief"]))
    m[4].metric("Marktkap.", fmt_big(row["Marktkap."]))
    if row["Risiken"]:
        st.warning("⚠️ Risiko-Flags: " + row["Risiken"])
    close = prices[pick].dropna()
    close = close[close.index >= close.index.max() - pd.DateOffset(years=lookback)]
    a, b = st.columns([3, 1])
    a.plotly_chart(charts.price_chart(close, "Kursverlauf", row.to_dict(), row["Währung"]), width="stretch")
    b.plotly_chart(charts.factor_bars(row, FACTORS), width="stretch")
    b.markdown(f"**Historisch:** {row['Hist. Status'] or '–'}  \n"
               f"Einbruch {fmt_pct(row['Hist. Einbruch'])}, Erholung {fmt_pct(row['Hist. Erholung'])}  \n"
               f"**News:** {row['News-Abgleich']}")
    hist = f.get("history")
    a, b = st.columns([3, 2])
    fc = charts.fundamentals_chart(hist, row["Währung"])
    if fc:
        a.plotly_chart(fc, width="stretch")
    mc = charts.margin_chart(hist)
    if mc:
        b.plotly_chart(mc, width="stretch")
    k = st.columns(6)
    k[0].metric("Margentrend (Pp.)", f"{row['Margentrend'] * 100:+.1f}" if pd.notna(row["Margentrend"]) else "–")
    k[1].metric("Umsatz Δ", fmt_pct(row["Umsatzwachstum"]))
    k[2].metric("Nettogewinn TTM", fmt_big(row["Nettogewinn TTM"]))
    k[3].metric("FCF TTM", fmt_big(row["FCF TTM"]))
    k[4].metric("KBV", f"{row['KBV']:.2f}" if pd.notna(row["KBV"]) else "–")
    tgt = row["Kursziel"]
    k[5].metric("Kursziel Analysten", de_num(tgt) if pd.notna(tgt) else "–",
                fmt_pct(tgt / row["Kurs"] - 1) if pd.notna(tgt) else None)
    if info.get("longBusinessSummary"):
        with st.expander("Unternehmensprofil"):
            st.write(info["longBusinessSummary"])
    if pick not in news_store:
        if st.button("📰 Nachrichten für diesen Titel laden"):
            news_store[pick] = news.load_news(pick, row["Name"])
            st.rerun()
    else:
        ndf = news_store[pick]
        st.markdown("**Aktuelle Nachrichten**")
        if ndf.empty:
            st.info("Keine Nachrichten gefunden.")
        else:
            st.dataframe(ndf[["Datum", "Titel", "Quelle", "Stimmung", "Kategorien", "Link"]].head(25),
                         hide_index=True, width="stretch",
                         column_config={"Datum": st.column_config.DatetimeColumn(format="DD.MM.YYYY"),
                                        "Stimmung": st.column_config.NumberColumn(format="%+.1f"),
                                        "Link": st.column_config.LinkColumn(display_text="öffnen")})

# --------------------------------------------------------------------------- #
# Signal-Check
# --------------------------------------------------------------------------- #
with tabs[6]:
    st.markdown("Wie gut hätten die **reinen Kurssignale** (Abschlag, Bodenbildung, Momentum) in der Vergangenheit "
                "funktioniert? An jedem Stichtag werden die Top-N-Kandidaten nach Score gewählt und deren "
                "Folgerendite mit allen Kandidaten und dem gesamten Universum verglichen.")
    a, b, c = st.columns(3)
    horizon = a.selectbox("Haltedauer", [3, 6, 12], index=2, format_func=lambda m: f"{m} Monate")
    bt_top = b.slider("Top-N je Stichtag", 3, 20, 10)
    step = c.selectbox("Abstand der Stichtage", [1, 3, 6], index=1, format_func=lambda m: f"{m} Monate")
    bt_key = (key, horizon, bt_top, step, lookback, cand_dd, tuple(weights[f] for f in analysis.PRICE_FACTORS))
    if st.button("▶️ Signal-Check starten") or st.session_state.get("bt_key") == bt_key:
        if st.session_state.get("bt_key") != bt_key:
            with st.spinner("Berechne …"):
                st.session_state["bt"] = analysis.signal_backtest(prices, params, horizon, bt_top, step)
                st.session_state["bt_key"] = bt_key
        periods, details = st.session_state["bt"]
        if periods.empty:
            st.info("Zu wenig Historie für den gewählten Zeitraum.")
        else:
            m = st.columns(4)
            m[0].metric("Ø Top-Auswahl", fmt_pct(periods["Top-Auswahl"].mean()))
            m[1].metric("Ø alle Kandidaten", fmt_pct(periods["Alle Kandidaten"].mean()))
            m[2].metric("Ø Universum", fmt_pct(periods["Gesamtes Universum"].mean()))
            beat = (periods["Top-Auswahl"] > periods["Gesamtes Universum"]).mean()
            m[3].metric("Stichtage Top > Universum", f"{beat:.0%}")
            st.plotly_chart(charts.backtest_lines(periods), width="stretch")
            st.dataframe(pct(periods, ["Top-Auswahl", "Alle Kandidaten", "Gesamtes Universum", "Trefferquote Top"]),
                         hide_index=True, width="stretch",
                         column_config={"Stichtag": st.column_config.DateColumn(format="DD.MM.YYYY"),
                                        **{k: PCT(k) for k in ["Top-Auswahl", "Alle Kandidaten", "Gesamtes Universum",
                                                               "Trefferquote Top"]}})
    st.caption("Grenzen: Fundamental- und Nachrichtendaten sind nicht historisch verfügbar und fehlen im Test. "
               "Das Universum enthält nur heute gelistete Titel (Survivorship-Bias), Kosten/Steuern sind nicht "
               "berücksichtigt. Ergebnisse der Vergangenheit sind keine Garantie für die Zukunft.")

# --------------------------------------------------------------------------- #
# Depots (Bitpanda, eToro)
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=300, show_spinner="Frage Broker-Konten ab …")
def load_broker_data(etoro_account: str):
    return brokers.fetch_all(etoro_account)


with tabs[7]:
    st.markdown("Bestände aus den angebundenen Broker-Konten, abgeglichen mit Turnaround-Score und Musterportfolio. "
                "Orders sind nur im **eToro-Demokonto** möglich (unten) – Echtgeld-Konten werden nur gelesen.")
    a, b = st.columns([3, 1])
    et_acc = a.radio("eToro-Konto", ["real", "demo"], format_func={"real": "Echtgeld", "demo": "Demo"}.get,
                     horizontal=True, key="etoro_account")
    if b.button("🔄 Depots aktualisieren", width="stretch"):
        load_broker_data.clear()
    statuses, hold = load_broker_data(et_acc)

    icons = {"verbunden": "🟢", "nicht konfiguriert": "⚪", "Fehler": "🔴"}
    for col, s in zip(st.columns(len(statuses)), statuses):
        with col.container(border=True):
            st.markdown(f"**{icons[s.status]} {s.name} · {s.konto}**  \n{s.status}")
            if s.summary:
                cur = s.summary.waehrung
                m1, m2 = st.columns(2)
                m1.metric("Positionen", s.summary.positionen)
                m2.metric(f"Cash ({cur})", fmt_big(s.summary.cash) if s.summary.cash is not None else "–")
                if s.summary.guv is not None:
                    st.metric(f"Unrealisierte GuV ({cur})", de_num(s.summary.guv))
            if s.meldung:
                st.caption(s.meldung)

    if hold.empty:
        st.info("Keine offenen Positionen in den verbundenen Konten.")
    else:
        sc = scored.set_index("Ticker")
        pf_set = set(pf["Ticker"]) if not pf.empty else set()
        hold["Im Scanner"] = hold["yahoo"].isin(sc.index)
        hold["Score"] = hold["yahoo"].map(sc["Score"])
        hold["Phase"] = hold["yahoo"].map(sc["Phase"])
        hold["Risiken"] = hold["yahoo"].map(sc["Risiken"]).fillna("")
        hold["Im Musterportfolio"] = hold["yahoo"].isin(pf_set)
        st.subheader("Positionen")
        st.dataframe(hold[["broker", "konto", "name", "symbol", "anlageklasse", "richtung", "menge", "einstandskurs",
                           "kurs", "investiert", "guv", "waehrung", "hebel", "Score", "Phase", "Risiken",
                           "Im Musterportfolio"]],
                     hide_index=True, width="stretch",
                     column_config={"broker": "Broker", "konto": "Konto", "name": "Name", "symbol": "Symbol",
                                    "anlageklasse": "Klasse", "richtung": "Richtung",
                                    "menge": st.column_config.NumberColumn("Menge", format="%.4f"),
                                    "einstandskurs": st.column_config.NumberColumn("Einstand", format="%.2f"),
                                    "kurs": st.column_config.NumberColumn("Kurs", format="%.2f"),
                                    "investiert": st.column_config.NumberColumn("Investiert", format="%.2f"),
                                    "guv": st.column_config.NumberColumn("GuV", format="%+.2f"),
                                    "waehrung": "Whg.", "hebel": st.column_config.NumberColumn("Hebel", format="%.0fx"),
                                    "Score": score_col(), "Im Musterportfolio": "Im Musterportfolio"})
        weak = hold[hold["Im Scanner"] & ((hold["Score"] < 40) | (hold["Risiken"] != ""))]
        if not weak.empty:
            st.warning("**Prüfen:** Gehaltene Titel mit schwachem Turnaround-Score (< 40) oder Risiko-Flags: "
                       + ", ".join(f"{r['name']} ({r['Score']:.0f}{', ' + r['Risiken'] if r['Risiken'] else ''})"
                                   for _, r in weak.iterrows()))
        outside = hold[~hold["Im Scanner"] & hold["anlageklasse"].isin(["Aktie", "ETF"])]
        if not outside.empty:
            st.caption("Nicht im gewählten Universum (Ticker ggf. über „Eigene Ticker“ ergänzen): "
                       + ", ".join(outside["yahoo"].dropna().unique()))

    if not pf.empty:
        held = set(hold["yahoo"].dropna()) if not hold.empty else set()
        missing = pf[~pf["Ticker"].isin(held)]
        st.subheader("Musterportfolio vs. Depot")
        st.markdown(f"{len(pf) - len(missing)} von {len(pf)} Musterportfolio-Titeln sind bereits im Depot.")
        if not missing.empty:
            st.dataframe(pct(missing, ["Gewicht"])[["Name", "Ticker", "Score", "Gewicht", "Phase"]], hide_index=True,
                         width="stretch", column_config={"Score": score_col(), "Gewicht": PCT("Zielgewicht")})

    # ---- eToro-DEMOkonto ans Musterportfolio angleichen ----------------------
    st.divider()
    st.subheader("🔁 eToro-Demokonto ans Musterportfolio angleichen")
    if st.session_state.pop("demo_sync_reset", False):  # nach Ausführung Schalter zurücksetzen
        st.session_state["demo_sync"] = False
        st.session_state["demo_confirm"] = False
    demo_client = brokers.EToroClient.from_env("demo")
    if demo_client is None:
        st.info("eToro ist nicht konfiguriert (ETORO_API_KEY und ETORO_USER_KEY in .env).")
    elif pf.empty:
        st.info("Kein Musterportfolio vorhanden – Kriterien in der Seitenleiste anpassen.")
    else:
        st.caption("Gilt ausschließlich für das **Demokonto** (Spielgeld) – das Echtgeld-Konto ist für Orders gesperrt. "
                   "Beim Einschalten wird ein Plan berechnet; ausgeführt wird erst nach deiner Bestätigung. "
                   "Käufe erfolgen als Market-Order ohne Hebel; bei geschlossener Börse wartet die Order auf die "
                   "Marktöffnung.")
        if st.toggle("Demo-Depot angleichen", key="demo_sync"):
            c1, c2, c3 = st.columns(3)
            mode = c1.radio("Volumen", ["Gesamtes Demo-Kapital", "Fester Betrag"], key="demo_budget_mode")
            amount = c2.number_input("Betrag (USD)", 100, 10_000_000, 10_000, 500, key="demo_amount",
                                     disabled=mode != "Fester Betrag")
            min_trade = c3.number_input("Mindest-Ordergröße (USD)", 10, 5_000, 50, 10, key="demo_min_trade",
                                        help="Kleinere Abweichungen werden nicht gehandelt.")
            close_others = st.checkbox("Positionen außerhalb des Musterportfolios schließen", value=True,
                                       key="demo_close_others")
            budget = float(amount) if mode == "Fester Betrag" else None
            plan_key = (tuple(zip(pf["Ticker"], pf["Gewicht"].round(4))), budget, min_trade, close_others)
            if st.button("🔄 Plan neu berechnen"):
                st.session_state.pop("demo_plan_key", None)
            if st.session_state.get("demo_plan_key") != plan_key:
                with st.spinner("Lese Demokonto und berechne Orders …"):
                    try:
                        st.session_state["demo_plan"] = rebalance.build_plan(
                            demo_client, pf, budget, float(min_trade), close_others=close_others)
                        st.session_state["demo_plan_err"] = None
                    except brokers.BrokerError as exc:
                        st.session_state["demo_plan"], st.session_state["demo_plan_err"] = None, str(exc)
                st.session_state["demo_plan_key"] = plan_key
            plan = st.session_state.get("demo_plan")
            if st.session_state.get("demo_plan_err"):
                st.error("Plan konnte nicht berechnet werden: " + st.session_state["demo_plan_err"])
            elif plan is not None:
                m = st.columns(4)
                m[0].metric("Demo-Kapital (USD)", de_num(plan.equity, 0))
                m[1].metric("Cash (USD)", de_num(plan.cash, 0))
                m[2].metric("Verkäufe (USD)", de_num(plan.verkaeufe, 0))
                m[3].metric("Käufe (USD)", de_num(plan.kaeufe, 0))
                for h in plan.hinweise:
                    st.caption("ℹ️ " + h)
                if not plan.uebersicht.empty:
                    st.dataframe(pct(plan.uebersicht, ["Zielgewicht"]), hide_index=True, width="stretch",
                                 column_config={"Zielgewicht": PCT("Zielgewicht"),
                                                **{c: st.column_config.NumberColumn(c, format="%.0f")
                                                   for c in ["Ist (USD)", "Ziel (USD)", "Nachher (USD)"]},
                                                "Änderung (USD)": st.column_config.NumberColumn(format="%+.0f")})
                if not plan.orders:
                    st.success("Das Demo-Depot entspricht bereits dem Musterportfolio – keine Orders nötig.")
                else:
                    st.markdown(f"**Geplante Orders ({len(plan.orders)})** – erst Verkäufe, dann Käufe:")
                    st.dataframe(plan.orders_frame(), hide_index=True, width="stretch",
                                 column_config={"aktion": "Aktion", "symbol": "Symbol", "name": "Name",
                                                "betrag": st.column_config.NumberColumn("Betrag (USD)", format="%.2f"),
                                                "units": st.column_config.NumberColumn("Stück", format="%.4f"),
                                                "position_id": st.column_config.NumberColumn("Position", format="%d"),
                                                "grund": "Grund"})
                    confirm = st.checkbox(f"Ich möchte diese {len(plan.orders)} Orders im eToro-**Demokonto** ausführen.",
                                          key="demo_confirm")
                    if st.button("✅ Orders im Demokonto ausführen", type="primary", disabled=not confirm):
                        bar = st.progress(0.0, text="Sende Orders …")
                        st.session_state["demo_results"] = rebalance.execute_plan(demo_client, plan,
                                                                                  progress=progress_cb(bar))
                        load_broker_data.clear()  # Depotanzeige mit neuen Positionen laden
                        st.session_state.pop("demo_plan_key", None)
                        st.session_state["demo_sync_reset"] = True
                        st.rerun()
        res = st.session_state.get("demo_results")
        if res is not None and not res.empty:
            ok = res["Status"].isin(["Ausgeführt", "Teilweise ausgeführt"]).sum()
            st.markdown(f"**Letzte Ausführung:** {ok} von {len(res)} Orders ausgeführt.")
            st.dataframe(res, hide_index=True, width="stretch",
                         column_config={"Betrag (USD)": st.column_config.NumberColumn(format="%.2f")})
            st.caption("Protokoll: portfolios/demo_sync_log.jsonl")

# --------------------------------------------------------------------------- #
# Methodik
# --------------------------------------------------------------------------- #
with tabs[8]:
    st.markdown(f"""
### Historische Turnarounds
Für jeden Handelstag wird der **Einbruch** (Kurs ggü. dem bisherigen Hoch) und die **Erholung** (bestes späteres
Kursniveau ggü. diesem Tag) berechnet. Ein Turnaround liegt vor, wenn ein Tag mit Einbruch ≥ {hist_dd:.0%} eine
Erholung ≥ {hist_rec:.0%} nach sich zieht. *Fundamental bestätigt* heißt: Rückkehr in die Gewinnzone oder operative
Marge > 2 Pp. über Vorjahr. Zu jedem Tiefpunkt lassen sich historische Nachrichten (Google News, ± 45 Tage) laden.

### Kandidaten-Score (0–100)
Kandidat ist, wer mindestens {cand_dd:.0%} unter dem Hoch des Analysezeitraums notiert. Sechs Teil-Scores (je 0–1)
werden gewichtet gemittelt; fehlende Daten werden ausgelassen statt mit 0 bewertet:
""" + "\n".join(f"- **{k}** – {v}" for k, v in analysis.FACTOR_HELP.items()) + """

**Risiko-Flags** verringern den Score multiplikativ: Fallendes Messer (×0,75), Negatives Eigenkapital (×0,85),
Hohe Verschuldung (×0,9), Cash-Burn (×0,9), Existenz-Nachrichten wie Insolvenz/Betrug (×0,5, im Portfolio
ausgeschlossen).

### News-Abgleich
Nachrichten aus Yahoo Finance und Google News (deutsch/englisch) werden mit einem Turnaround-Lexikon bewertet
(z. B. *Prognose angehoben*, *neuer CEO*, *Restrukturierung* positiv; *Gewinnwarnung*, *Kapitalerhöhung*,
*Insolvenz* negativ). Der Abgleich vergleicht Kurs-/Fundamentalsignale mit der Nachrichtenlage:
✅ bestätigt · ⚠️ News widersprechen · 💡 News laufen den Zahlen voraus · 🔻 beides schwach.

### Musterportfolio
Die besten Kandidaten über dem Mindest-Score, maximal N je Sektor; Gewichtung nach Score, Score/Volatilität oder
gleich, mit Obergrenze je Titel. Snapshots werden in `portfolios/` gespeichert und können später verfolgt werden.

### Datenquellen & Grenzen
Yahoo Finance (über `yfinance`) und Google-News-RSS – kostenlos, aber ohne Gewähr; Fundamentaldaten umfassen meist
nur 4 Jahre bzw. 5–6 Quartale. Daten werden lokal in `cache/` zwischengespeichert (Kurse 12 h, Fundamentaldaten 24 h,
News 6 h).

> **Dies ist ein Analyse- und Lernwerkzeug, keine Anlageberatung.** Turnaround-Investments sind besonders riskant –
> viele Unternehmen erholen sich nie.
""")
