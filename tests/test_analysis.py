import numpy as np
import pandas as pd
import pytest

from turnaround import analysis, news, portfolio


def _series(values):
    idx = pd.bdate_range("2021-01-01", periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_detect_successful_turnaround():
    # 100 -> 40 (−60 %) -> 120 (+200 %)
    vals = np.concatenate([np.linspace(100, 40, 300), np.linspace(40, 120, 300)])
    res = analysis.detect_turnaround(_series(vals), min_dd=0.4, min_rec=0.6)
    assert res["Turnaround"]
    assert res["Einbruch"] == pytest.approx(-0.6, abs=1e-6)
    assert res["Erholung"] == pytest.approx(2.0, abs=1e-6)
    assert res["Status"] == "Erfolgreich (neues Hoch)"


def test_detect_no_drawdown():
    vals = np.linspace(100, 150, 400)
    res = analysis.detect_turnaround(_series(vals))
    assert not res["Turnaround"]
    assert res["Status"] == "Kein tiefer Einbruch"


def test_detect_without_recovery():
    vals = np.concatenate([np.linspace(100, 30, 400), np.full(50, 31.0)])
    res = analysis.detect_turnaround(_series(vals), 0.4, 0.6)
    assert not res["Turnaround"]
    assert res["Status"] == "Ohne Erholung"


def test_price_signals_and_scores_in_range():
    rng = np.random.default_rng(0)
    vals = np.concatenate([np.linspace(100, 45, 500), np.linspace(45, 60, 250)]) * (1 + rng.normal(0, 0.005, 750))
    ps = analysis.price_signals(_series(vals))
    assert ps["Abstand Hoch"] < -0.3
    for fn in (analysis.score_discount, analysis.score_stabilization, analysis.score_momentum):
        assert 0 <= fn(ps) <= 1
    assert analysis.phase_label(ps, analysis.score_momentum(ps)) in {"Frühe Erholung", "Bodenbildung"}


def test_news_lexicon():
    s, hits, cats = news.score_text("Lufthansa hebt Prognose an – neuer CEO setzt auf Sparprogramm")
    assert s > 1.5 and "Prognose" in cats and "Management" in cats
    s, _, cats = news.score_text("Varta: Insolvenz in Eigenverwaltung beantragt")
    assert s < 0 and "Existenz" in cats
    # Teilwort darf nicht treffen ("ideal" != "deal")
    assert news.score_text("an ideal setup")[0] == 0


def test_cap_weights():
    w = portfolio.cap_weights(np.array([0.7, 0.1, 0.1, 0.1]), 0.4)
    assert w.max() <= 0.4 + 1e-9 and w.sum() == pytest.approx(1)


def test_build_portfolio_sector_limit():
    df = pd.DataFrame({
        "Ticker": list("ABCDE"), "Name": list("ABCDE"), "Sektor": ["X", "X", "X", "Y", "Z"],
        "Score": [90, 85, 80, 70, 60], "Kandidat": True, "Risiken": "", "Vola 1J": 0.3, "Kurs": 10.0,
    })
    pf = portfolio.build_portfolio(df, n=4, max_per_sector=2, max_weight=0.5, min_score=0)
    assert list(pf["Ticker"]) == ["A", "B", "D", "E"]
    assert pf["Gewicht"].sum() == pytest.approx(1)
