"""Tests für das Angleichen des eToro-Demokontos (ohne Netzwerk)."""
import pandas as pd
import pytest

from turnaround.brokers import BrokerError, EToroClient
from turnaround.brokers import rebalance as rb

RESOLVED = {"SAP.DE": {"instrumentId": 1202, "displayName": "SAP"},
            "ZAL.DE": {"instrumentId": 2521, "displayName": "Zalando"}}
TARGETS = [{"symbol": "SAP.DE", "name": "SAP", "weight": 0.5}, {"symbol": "ZAL.DE", "name": "Zalando", "weight": 0.5}]


def pos(pid, iid, value, units=10, is_buy=True):
    return {"position_id": pid, "instrument_id": iid, "units": units, "value": value, "is_buy": is_buy}


def test_plan_from_empty_account_buys_targets():
    plan = rb.plan_rebalance(TARGETS, RESOLVED, [], {}, cash=10_000)
    assert [(o.aktion, o.symbol, round(o.betrag)) for o in plan.orders] == [("Kaufen", "SAP.DE", 5000),
                                                                           ("Kaufen", "ZAL.DE", 5000)]


def test_plan_closes_foreign_positions_and_trims_overweight():
    positions = [pos(1, 999, 2000), pos(2, 1202, 6000, units=30), pos(3, 2521, 1000)]
    plan = rb.plan_rebalance(TARGETS, RESOLVED, positions, {1202: 200.0}, cash=1000, names={999: "Alt AG"})
    acts = {(o.aktion, o.symbol) for o in plan.orders}
    assert ("Schließen", "999") in acts                     # fremde Position raus
    trim = next(o for o in plan.orders if o.symbol == "SAP.DE")
    assert trim.aktion == "Teilverkauf" and trim.betrag == pytest.approx(1000) and trim.units == pytest.approx(5)
    buy = next(o for o in plan.orders if o.symbol == "ZAL.DE")
    assert buy.aktion == "Kaufen" and buy.betrag == pytest.approx(4000)
    # Verkäufe stehen vor Käufen
    kinds = [o.ist_verkauf for o in plan.orders]
    assert kinds == sorted(kinds, reverse=True)


def test_plan_skips_untradable_and_renormalizes():
    targets = TARGETS + [{"symbol": "SRT3.DE", "name": "Sartorius", "weight": 0.5}]
    plan = rb.plan_rebalance(targets, RESOLVED, [], {}, cash=9000)
    assert any("Sartorius" in h for h in plan.hinweise)
    assert sum(o.betrag for o in plan.orders) == pytest.approx(9000)


def test_plan_respects_tolerance_and_budget_cap():
    positions = [pos(1, 1202, 2490), pos(2, 2521, 2510)]
    plan = rb.plan_rebalance(TARGETS, RESOLVED, positions, {}, cash=0, min_trade=50)
    assert plan.orders == []
    plan = rb.plan_rebalance(TARGETS, RESOLVED, [], {}, cash=1000, budget=50_000)
    assert plan.budget == 1000 and any("begrenzt" in h for h in plan.hinweise)


def test_plan_closes_short_on_target():
    plan = rb.plan_rebalance(TARGETS, RESOLVED, [pos(7, 1202, 500, is_buy=False)], {}, cash=10_000)
    assert any(o.aktion == "Schließen" and o.position_id == 7 for o in plan.orders)


def test_real_account_cannot_trade():
    c = EToroClient("k", "u", "real")
    c.post = lambda *a, **k: pytest.fail("darf nicht senden")
    with pytest.raises(BrokerError):
        c.open_demo_order(1202, 100)
    with pytest.raises(BrokerError):
        c.close_demo_position(1, 1202)
    with pytest.raises(BrokerError):
        rb.execute_plan(c, rb.Plan([], pd.DataFrame(), 0, 0, 0))


def test_open_order_body_and_path():
    c = EToroClient("k", "u", "demo")
    sent = {}
    c.post = lambda path, body: sent.update(path=path, body=body) or {"orderId": 5}
    c.open_demo_order(1202, 123.456)
    assert sent["path"] == "/api/v2/trading/execution/demo/orders"
    assert sent["body"]["instrumentId"] == 1202 and sent["body"]["amount"] == 123.46
    assert sent["body"]["transaction"] == "buy" and sent["body"]["leverage"] == 1
    c.close_demo_position(77, 1202)
    assert sent["path"].endswith("/demo/market-close-orders/positions/77")
    assert sent["body"] == {"InstrumentID": 1202, "UnitsToDeduct": None}


def test_resolve_symbols_prefers_main_listing(monkeypatch):
    c = EToroClient("k", "u", "demo")
    monkeypatch.setattr(c, "get", lambda path, params=None: {"results": [
        {"instrumentId": 1013293, "symbol": "SAP.DE"}, {"instrumentId": 1202, "symbol": "SAP.DE"}]})
    assert c.resolve_symbols(["sap.de"])["SAP.DE"]["instrumentId"] == 1202


class FakeDemo(EToroClient):
    def __init__(self):
        super().__init__("k", "u", "demo")
        self.calls = []

    def close_demo_position(self, pid, iid, units=None):
        self.calls.append(("close", pid))
        return {"orderForClose": {"orderID": 100 + pid}}

    def demo_close_status(self, oid):
        return {"statusID": 3}

    def portfolio(self):
        return {"credit": 500.0}

    def open_demo_order(self, iid, amount):
        self.calls.append(("open", iid, round(amount, 2)))
        return {"orderId": 9}

    def demo_open_status(self, oid):
        return {"status": {"id": 4, "errorMessage": "Mindestbetrag unterschritten"}}


def test_execute_plan_sells_first_scales_buys_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "LOG_FILE", tmp_path / "log.jsonl")
    plan = rb.Plan([rb.Order("Kaufen", "ZAL.DE", "Zalando", 2521, 1000.0),
                    rb.Order("Schließen", "X", "X", 999, 300.0, position_id=1)], pd.DataFrame(), 0, 0, 0)
    plan.orders.sort(key=lambda o: not o.ist_verkauf)
    c = FakeDemo()
    res = rb.execute_plan(c, plan, poll_interval=0, pause=0)
    assert c.calls[0] == ("close", 1)
    assert c.calls[1][0] == "open" and c.calls[1][2] == pytest.approx(495.0)  # auf Guthaben gekürzt
    assert list(res["Status"]) == ["Ausgeführt", "Abgelehnt"]
    assert "Mindestbetrag" in res.iloc[1]["Meldung"]
    assert (tmp_path / "log.jsonl").exists()


def test_execute_aborts_on_missing_write_permission(tmp_path, monkeypatch):
    from turnaround.brokers.etoro import WRITE_PERMISSION_HINT
    monkeypatch.setattr(rb, "LOG_FILE", tmp_path / "log.jsonl")

    class NoWrite(FakeDemo):
        def open_demo_order(self, iid, amount):
            self.calls.append(("open", iid))
            raise BrokerError(WRITE_PERMISSION_HINT)

    c = NoWrite()
    plan = rb.Plan([rb.Order("Kaufen", s, s, i, 100.0) for s, i in (("A", 1), ("B", 2), ("C", 3))],
                   pd.DataFrame(), 0, 0, 0)
    res = rb.execute_plan(c, plan, poll_interval=0, pause=0)
    assert len(c.calls) == 1
    assert list(res["Status"]) == ["Fehler", "Nicht gesendet", "Nicht gesendet"]


def test_demo_write_keys_take_precedence(monkeypatch):
    monkeypatch.setattr("turnaround.brokers.etoro.env", lambda n: {
        "ETORO_API_KEY": "read", "ETORO_USER_KEY": "readu",
        "ETORO_DEMO_API_KEY": "write", "ETORO_DEMO_USER_KEY": "writeu"}.get(n))
    assert EToroClient.from_env("demo")._api_key == "write"
    assert EToroClient.from_env("real")._api_key == "read"


def test_write_user_key_only_for_orders(monkeypatch):
    monkeypatch.setattr("turnaround.brokers.etoro.env", lambda n: {
        "ETORO_API_KEY": "api", "ETORO_USER_KEY": "readu", "ETORO_API_WRITE": "writeu"}.get(n))
    assert EToroClient.from_env("real")._write_user_key is None
    c = EToroClient.from_env("demo")
    seen = []
    monkeypatch.setattr(c, "_request", lambda *a, **k: seen.append(c._headers()["x-user-key"]) or {})
    c.get("/x")
    c.open_demo_order(1, 50)
    c.get("/y")
    assert seen == ["readu", "writeu", "readu"]
