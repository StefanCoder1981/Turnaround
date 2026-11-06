import numpy as np
import pandas as pd

from turnaround import portfolio


def test_backward_performance_one_week():
    idx = pd.bdate_range("2026-01-01", periods=300)
    prices = pd.DataFrame({"A": np.linspace(10, 40, 300), "B": 20.0}, index=idx)
    pf = pd.DataFrame({"Ticker": ["A", "B"], "Gewicht": [0.5, 0.5]})
    w = portfolio.backward_performance(prices, pf, weeks=1)
    assert w.index.min() >= idx.max() - pd.DateOffset(weeks=1)
    assert len(w) <= 6 and w.iloc[0] == 1.0
    m = portfolio.backward_performance(prices, pf, months=3)
    assert len(m) > len(w)
