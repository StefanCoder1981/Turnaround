import numpy as np
import pandas as pd

from turnaround import analysis, portfolio


def _prices():
    idx = pd.bdate_range("2023-01-02", periods=900)
    t = np.arange(900)
    return pd.DataFrame({
        "UP": np.linspace(10, 40, 900),                              # steigt stetig
        "FLAT": np.full(900, 20.0),
        "DIP": 50 - 30 * np.sin(np.minimum(t, 700) / 700 * np.pi / 2) + np.maximum(t - 700, 0) * 0.05,
    }, index=idx)


def test_backward_performance_one_week():
    prices = _prices()
    pf = pd.DataFrame({"Ticker": ["UP", "FLAT"], "Gewicht": [0.5, 0.5]})
    w = portfolio.backward_performance(prices, pf, weeks=1)
    assert w.index.min() == portfolio.period_start(prices, weeks=1)
    assert len(w) <= 7 and w.iloc[0] == 1.0
    assert len(portfolio.backward_performance(prices, pf, months=3)) > len(w)


def test_period_start_and_returns():
    prices = _prices()
    start = portfolio.period_start(prices, months=3)
    assert start <= prices.index.max() - pd.DateOffset(months=3)
    r = portfolio.period_returns(prices, ["UP", "FLAT"], start)
    assert r["FLAT"] == 0 and r["UP"] == prices["UP"].iloc[-1] / prices.loc[start, "UP"] - 1


def test_score_as_of_uses_only_past_prices():
    prices = _prices()
    asof = prices.index[600]
    static = pd.DataFrame({"Ticker": ["UP", "FLAT", "DIP"], "Name": ["Up", "Flat", "Dip"],
                           "Sektor": ["A", "B", "C"], "Marktkap.": [1e9, 1e9, 1e9]})
    df = analysis.score_as_of(prices, analysis.Params(), asof, static)
    assert set(df["Ticker"]) == {"UP", "FLAT", "DIP"}
    assert (df["Stichtag"] <= asof).all()
    dip = df.set_index("Ticker").loc["DIP"]
    assert dip["Kurs"] == prices.loc[asof, "DIP"] and dip["Kandidat"]   # damals tief unter dem Hoch
    assert not df.set_index("Ticker").loc["UP", "Kandidat"]
    # Später geänderte Kurse dürfen das Ergebnis nicht beeinflussen
    changed = prices.copy()
    changed.iloc[601:] *= 3
    pd.testing.assert_frame_equal(df, analysis.score_as_of(changed, analysis.Params(), asof, static))
    pf = portfolio.build_portfolio(df, n=3, min_score=0)
    assert list(pf["Ticker"]) == ["DIP"] and abs(pf["Gewicht"].sum() - 1) < 1e-9
