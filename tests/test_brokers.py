"""Tests der Broker-Clients mit simulierten API-Antworten (keine Netzwerkzugriffe)."""
import pytest

from turnaround.brokers import BitpandaClassicClient, BrokerError, EToroClient, FusionClient, fetch_all
from turnaround.brokers.etoro import yahoo_symbol


def fake_get(responses):
    def _get(path, params=None):
        for prefix, resp in responses.items():
            if path.startswith(prefix):
                return resp(params) if callable(resp) else resp
        raise BrokerError(f"{path}: HTTP 404 – unbekannt")
    return _get


def test_etoro_holdings_aggregates_positions(monkeypatch):
    c = EToroClient("k", "u", "real")
    pnl = {"clientPortfolio": {
        "credit": 1000.0, "unrealizedPnL": 55.0, "mirrors": [],
        "positions": [
            {"positionID": 1, "instrumentID": 1001, "isBuy": True, "units": 2, "openRate": 100, "amount": 200,
             "leverage": 1, "unrealizedPnL": {"pnL": 40.0}},
            {"positionID": 2, "instrumentID": 1001, "isBuy": True, "units": 1, "openRate": 130, "amount": 130,
             "leverage": 1, "unrealizedPnL": {"pnL": 10.0}},
            {"positionID": 3, "instrumentID": 100000, "isBuy": True, "units": 0.01, "openRate": 60000,
             "amount": 600, "leverage": 1, "unrealizedPnL": {"pnL": 5.0}},
        ]}}
    instruments = {"results": [
        {"instrumentId": 1001, "displayName": "Apple", "type": "Stocks", "symbol": "AAPL"},
        {"instrumentId": 100000, "displayName": "Bitcoin", "type": "Crypto", "symbol": "BTC"},
    ]}
    rates = {"results": [{"instrumentId": 1001, "bid": 120.0, "ask": 120.1}]}
    monkeypatch.setattr(c, "get", fake_get({"/api/v1/trading/info/real/pnl": pnl,
                                            "/api/v2/market-data/instruments": instruments,
                                            "/api/v2/market-data/rates": rates}))
    s, items = c.holdings()
    assert s.cash == 1000.0 and s.guv == 55.0 and s.positionen == 2
    aapl = next(h for h in items if h.symbol == "AAPL")
    assert aapl.menge == 3 and aapl.einstandskurs == pytest.approx(110)
    assert aapl.guv == 50 and aapl.wert == 380 and aapl.kurs == 120.0 and aapl.yahoo == "AAPL"
    btc = next(h for h in items if h.symbol == "BTC")
    assert btc.anlageklasse == "Krypto" and btc.yahoo == "BTC-USD"


def test_etoro_demo_uses_demo_paths(monkeypatch):
    c = EToroClient("k", "u", "demo")
    seen = []
    monkeypatch.setattr(c, "get", lambda path, params=None: seen.append(path) or {"clientPortfolio": {}})
    c.holdings()
    assert seen == ["/api/v1/trading/info/demo/pnl"]


def test_etoro_headers_have_fresh_request_id():
    c = EToroClient("k", "u")
    assert c._headers()["x-request-id"] != c._headers()["x-request-id"]
    with pytest.raises(ValueError):
        EToroClient("k", "u", "paper")


def test_yahoo_symbol_mapping():
    assert yahoo_symbol("BAYN.DE", "Stocks") == "BAYN.DE"
    assert yahoo_symbol("eth", "Crypto") == "ETH-USD"
    assert yahoo_symbol("EURUSD", "Forex") is None


def test_fusion_holdings_values_coins(monkeypatch):
    c = FusionClient("k")
    balances = [{"symbol": "BTC", "available": "0.5", "locked": "0.1"}, {"symbol": "EUR", "total": "250"},
                {"symbol": "ETH", "total": "0"}]
    tickers = [{"pair": "BTC-EUR", "price": "60000"}]
    monkeypatch.setattr(c, "get", fake_get({"/v1/account/balances": balances, "/v1/tickers": tickers}))
    s, items = c.holdings()
    assert s.cash == 250 and len(items) == 1
    assert items[0].menge == pytest.approx(0.6) and items[0].wert == pytest.approx(36000)
    assert items[0].yahoo == "BTC-EUR"


def test_classic_bitpanda_resolves_assets(monkeypatch):
    c = BitpandaClassicClient("k")
    portfolio = {"data": [{"asset_id": "a1", "balance": {"value": "3"}, "average_buy_price": {"value": "90"},
                           "invested_amount": {"value": "270"}, "total_return": {"value": "30"},
                           "currency_balance": {"value": "300", "currency_id": "c1"}}]}
    assets = {"data": [{"id": "a1", "name": "Microsoft", "symbol": "MSFT", "type": "SECURITY", "group": "STOCK"},
                       {"id": "c1", "name": "Euro", "symbol": "EUR", "type": "FIAT"}]}
    monkeypatch.setattr(c, "get", fake_get({"/v1/portfolio": portfolio, "/v1/assets": assets}))
    s, items = c.holdings()
    h = items[0]
    assert h.name == "Microsoft" and h.anlageklasse == "Aktie" and h.wert == 300 and h.kurs == 100
    assert s.guv == 30


def test_fetch_all_reports_missing_config(monkeypatch):
    for k in ("FUSION_API_KEY", "BITPANDA_API_KEY", "ETORO_API_KEY", "ETORO_USER_KEY"):
        monkeypatch.setenv(k, "")
    monkeypatch.setattr("turnaround.brokers.load_env", lambda: None)
    statuses, df = fetch_all()
    assert [s.status for s in statuses] == ["nicht konfiguriert"] * 3
    assert df.empty


def test_errors_never_contain_keys(monkeypatch):
    import requests

    class R:
        status_code = 401
        headers = {}
        text = "unauthorized"

        def json(self):
            return {"errors": [{"title": "Credentials / Access token wrong"}]}

    c = FusionClient("SUPER-SECRET-KEY")
    monkeypatch.setattr(c.session, "request", lambda *a, **k: R())
    with pytest.raises(BrokerError) as exc:
        c.account()
    assert "SUPER-SECRET" not in str(exc.value) and "401" in str(exc.value)
