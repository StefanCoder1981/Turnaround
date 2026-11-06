"""Angleichen des eToro-DEMOkontos an das Musterportfolio.

Ablauf: plan_rebalance() berechnet aus Ist-Positionen und Zielgewichten die nötigen Orders (rein, ohne
Netz → testbar). execute_plan() führt sie im Demokonto aus: erst Verkäufe/Schließungen, dann Käufe
(begrenzt auf das danach verfügbare Guthaben). Orders laufen bei eToro asynchron und werden abgefragt.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from .base import BrokerError, to_float
from .etoro import WRITE_PERMISSION_HINT, EToroClient

LOG_FILE = Path(__file__).resolve().parents[2] / "portfolios" / "demo_sync_log.jsonl"

STATUS = {1: "Empfangen", 2: "Platziert", 3: "Ausgeführt", 4: "Abgelehnt", 5: "Teilweise ausgeführt",
          6: "Stornierung ausstehend", 7: "Storniert", 8: "Abgelaufen", 9: "Storniert (teilw. ausgeführt)",
          10: "Abgelehnt (teilw. ausgeführt)", 11: "Wartet auf Marktöffnung", 12: "Wartet auf Triggerkurs"}
FINAL = {3, 4, 7, 8, 9, 10, 11}  # 11: Markt geschlossen – Order liegt bei eToro, Abfragen sinnlos


@dataclass
class Order:
    aktion: str  # "Kaufen" | "Schließen" | "Teilverkauf"
    symbol: str
    name: str
    instrument_id: int
    betrag: float  # USD, positiv
    position_id: int | None = None
    units: float | None = None  # nur Teilverkauf
    grund: str = ""

    @property
    def ist_verkauf(self) -> bool:
        return self.aktion != "Kaufen"


@dataclass
class Plan:
    orders: list[Order]
    uebersicht: pd.DataFrame
    budget: float
    cash: float
    equity: float
    hinweise: list[str] = field(default_factory=list)

    @property
    def verkaeufe(self) -> float:
        return sum(o.betrag for o in self.orders if o.ist_verkauf)

    @property
    def kaeufe(self) -> float:
        return sum(o.betrag for o in self.orders if not o.ist_verkauf)

    def orders_frame(self) -> pd.DataFrame:
        cols = ["aktion", "symbol", "name", "betrag", "units", "position_id", "grund"]
        return pd.DataFrame([asdict(o) for o in self.orders], columns=list(Order.__dataclass_fields__))[cols]


# --------------------------------------------------------------------------- #
# Planung
# --------------------------------------------------------------------------- #
def own_positions(client_portfolio: dict) -> list[dict]:
    """Eigene Positionen (ohne CopyTrader) mit aktuellem Wert in USD."""
    out = []
    for p in client_portfolio.get("positions") or []:
        if p.get("mirrorID") or p.get("mirrorId"):
            continue
        pid = p.get("positionID", p.get("positionId"))
        iid = p.get("instrumentID", p.get("instrumentId"))
        if pid is None or iid is None:
            continue
        amount = to_float(p.get("amount")) or 0.0
        u = p.get("unrealizedPnL")
        pnl = to_float(u.get("pnL")) if isinstance(u, dict) else to_float(p.get("pnL"))
        out.append({"position_id": int(pid), "instrument_id": int(iid), "units": to_float(p.get("units")) or 0.0,
                    "value": amount + (pnl or 0.0), "is_buy": bool(p.get("isBuy", True))})
    return out


def plan_rebalance(targets: list[dict], resolved: dict[str, dict], positions: list[dict], prices: dict[int, float],
                   cash: float, budget: float | None = None, min_trade: float = 25.0, tolerance: float = 0.05,
                   close_others: bool = True, names: dict[int, str] | None = None) -> Plan:
    """targets: [{"symbol", "name", "weight"}]; resolved: Symbol -> eToro-Instrument (instrumentId, displayName)."""
    names = dict(names or {})
    hinweise: list[str] = []
    equity = cash + sum(p["value"] for p in positions)
    requested = budget
    budget = equity if requested is None else min(float(requested), equity)
    if requested is not None and float(requested) > equity:
        hinweise.append(f"Betrag auf das verfügbare Demo-Kapital ({equity:,.0f} USD) begrenzt.")

    tradable = []
    for t in targets:
        inst = resolved.get(t["symbol"].upper())
        if inst is None:
            hinweise.append(f"{t['name']} ({t['symbol']}) ist bei eToro nicht handelbar – übersprungen.")
            continue
        names[int(inst["instrumentId"])] = inst.get("displayName") or t["name"]
        tradable.append({**t, "iid": int(inst["instrumentId"])})
    wsum = sum(t["weight"] for t in tradable)
    for t in tradable:  # nicht handelbare Titel herausrechnen
        t["weight"] = t["weight"] / wsum if wsum else 0.0

    by_iid: dict[int, list[dict]] = {}
    for p in positions:
        by_iid.setdefault(p["instrument_id"], []).append(p)
    target_iids = {t["iid"] for t in tradable}
    sym_of = {t["iid"]: t["symbol"] for t in tradable}

    orders: list[Order] = []
    rows = []
    # 1) Positionen außerhalb des Musterportfolios und Short-Positionen schließen
    for iid, ps in by_iid.items():
        for p in ps:
            if iid not in target_iids and close_others:
                orders.append(Order("Schließen", sym_of.get(iid, str(iid)), names.get(iid, str(iid)), iid,
                                    p["value"], p["position_id"], grund="nicht im Musterportfolio"))
            elif iid in target_iids and not p["is_buy"]:
                orders.append(Order("Schließen", sym_of[iid], names.get(iid, str(iid)), iid, p["value"],
                                    p["position_id"], grund="Short-Position, Ziel ist Long"))
        if iid not in target_iids:
            rows.append({"Symbol": sym_of.get(iid, str(iid)), "Name": names.get(iid, str(iid)), "Zielgewicht": 0.0,
                         "Ist (USD)": sum(p["value"] for p in ps), "Ziel (USD)": 0.0 if close_others else None})

    # 2) Zielpositionen auf Zielwert bringen
    for t in tradable:
        iid = t["iid"]
        longs = sorted([p for p in by_iid.get(iid, []) if p["is_buy"]], key=lambda p: -p["value"])
        ist = sum(p["value"] for p in longs)
        ziel = t["weight"] * budget
        diff = ziel - ist
        rows.append({"Symbol": t["symbol"], "Name": names.get(iid, t["name"]), "Zielgewicht": t["weight"],
                     "Ist (USD)": ist, "Ziel (USD)": ziel})
        if abs(diff) < max(min_trade, tolerance * ziel):
            continue
        if diff > 0:
            orders.append(Order("Kaufen", t["symbol"], names.get(iid, t["name"]), iid, diff, grund="unter Zielgewicht"))
            continue
        remaining = -diff
        for p in longs:
            if remaining < min_trade:
                break
            if p["value"] <= remaining + min_trade:
                orders.append(Order("Schließen", t["symbol"], names.get(iid, t["name"]), iid, p["value"],
                                    p["position_id"], grund="über Zielgewicht"))
                remaining -= p["value"]
            else:
                price = prices.get(iid) or (p["value"] / p["units"] if p["units"] else None)
                if not price:
                    hinweise.append(f"Kein Kurs für {t['symbol']} – Teilverkauf übersprungen.")
                    break
                orders.append(Order("Teilverkauf", t["symbol"], names.get(iid, t["name"]), iid, remaining,
                                    p["position_id"], units=remaining / price, grund="über Zielgewicht"))
                remaining = 0
                break

    # 3) Käufe auf das verfügbare Guthaben nach den Verkäufen begrenzen
    sells = sum(o.betrag for o in orders if o.ist_verkauf)
    buys = [o for o in orders if not o.ist_verkauf]
    available = cash + sells
    total_buys = sum(o.betrag for o in buys)
    if total_buys > available > 0:
        f = available / total_buys
        for o in buys:
            o.betrag *= f
        hinweise.append(f"Käufe auf das verfügbare Guthaben gekürzt (Faktor {f:.2f}).")
    elif total_buys > 0 and available <= 0:
        for o in buys:
            o.betrag = 0
        hinweise.append("Kein Guthaben für Käufe verfügbar.")
    dropped = [o for o in buys if o.betrag < min_trade]
    if dropped:
        hinweise.append(f"{len(dropped)} Kauf/Käufe unter {min_trade:.0f} USD entfallen.")
    orders = [o for o in orders if o.ist_verkauf] + [o for o in buys if o.betrag >= min_trade]

    df = pd.DataFrame(rows)
    if not df.empty:
        delta = {}
        for o in orders:
            delta[o.symbol] = delta.get(o.symbol, 0.0) + (o.betrag if not o.ist_verkauf else -o.betrag)
        df["Änderung (USD)"] = df["Symbol"].map(delta).fillna(0.0)
        df["Nachher (USD)"] = df["Ist (USD)"] + df["Änderung (USD)"]
    return Plan(orders, df, budget, cash, equity, hinweise)


def build_plan(client: EToroClient, portfolio: pd.DataFrame, budget: float | None = None, min_trade: float = 25.0,
               tolerance: float = 0.05, close_others: bool = True) -> Plan:
    """Liest das Demokonto und berechnet den Plan für das gegebene Musterportfolio (Spalten Ticker, Name, Gewicht)."""
    if client.account != "demo":
        raise BrokerError("Angleichen ist nur für das eToro-Demokonto vorgesehen.")
    cp = client.pnl()
    positions = own_positions(cp)
    targets = [{"symbol": r.Ticker, "name": r.Name, "weight": float(r.Gewicht)} for r in portfolio.itertuples()]
    resolved = client.resolve_symbols([t["symbol"] for t in targets])
    iids = sorted({p["instrument_id"] for p in positions} | {int(i["instrumentId"]) for i in resolved.values()})
    meta = client.instruments(ids=[p["instrument_id"] for p in positions]) if positions else {}
    rates = client.rates(iids) if iids else {}
    prices = {iid: to_float(r.get("bid")) for iid, r in rates.items() if to_float(r.get("bid"))}
    names = {iid: m.get("displayName", "") for iid, m in meta.items()}
    sym_names = {int(m["instrumentId"]): m.get("symbol", "") for m in meta.values()}
    plan = plan_rebalance(targets, resolved, positions, prices, to_float(cp.get("credit")) or 0.0, budget,
                          min_trade, tolerance, close_others, names)
    for o in plan.orders:  # Symbole fremder Positionen lesbar machen
        if o.symbol == str(o.instrument_id) and o.instrument_id in sym_names:
            o.symbol = sym_names[o.instrument_id]
    if not plan.uebersicht.empty:
        plan.uebersicht["Symbol"] = [sym_names.get(int(s), s) if str(s).isdigit() else s
                                     for s in plan.uebersicht["Symbol"]]
    if cp.get("mirrors"):
        plan.hinweise.append("CopyTrader-Positionen werden nicht verändert.")
    return plan


# --------------------------------------------------------------------------- #
# Ausführung (nur Demokonto)
# --------------------------------------------------------------------------- #
def _status_id(resp: dict) -> tuple[int | None, str]:
    if not isinstance(resp, dict):
        return None, ""
    for container in (resp, resp.get("order") or {}, resp.get("orderForOpen") or {}, resp.get("orderForClose") or {}):
        st = container.get("status")
        if isinstance(st, dict) and st.get("id") is not None:
            return int(st["id"]), str(st.get("errorMessage") or st.get("errorCode") or "")
        for k in ("statusID", "statusId"):
            if container.get(k) is not None:
                return int(container[k]), str(container.get("errorMessage") or "")
    return None, ""


def _poll(fetch: Callable[[], dict], timeout: float, interval: float) -> tuple[int | None, str]:
    deadline = time.monotonic() + timeout
    sid, msg = None, ""
    while time.monotonic() < deadline:
        try:
            sid, msg = _status_id(fetch())
        except BrokerError as exc:
            msg = str(exc)
        if sid in FINAL:
            break
        time.sleep(interval)
    return sid, msg


def execute_plan(client: EToroClient, plan: Plan, progress: Callable[[float, str], None] | None = None,
                 poll_timeout: float = 30.0, poll_interval: float = 2.0, pause: float = 3.2) -> pd.DataFrame:
    """Führt den Plan im DEMOkonto aus. pause hält das Limit von 20 Order-Anfragen/Minute ein."""
    if client.account != "demo":
        raise BrokerError("Ausführung ist nur im eToro-Demokonto erlaubt.")
    results = []
    n = len(plan.orders)
    sells = [o for o in plan.orders if o.ist_verkauf]
    buys = [o for o in plan.orders if not o.ist_verkauf]

    abort = {"grund": ""}

    def run(o: Order, i: int):
        if abort["grund"]:
            results.append({"Aktion": o.aktion, "Symbol": o.symbol, "Name": o.name, "Betrag (USD)": round(o.betrag, 2),
                            "OrderID": None, "Status": "Nicht gesendet", "Meldung": abort["grund"]})
            return
        if progress:
            progress(i / max(n, 1), f"{o.aktion} {o.symbol} …")
        row = {"Aktion": o.aktion, "Symbol": o.symbol, "Name": o.name, "Betrag (USD)": round(o.betrag, 2),
               "OrderID": None, "Status": "", "Meldung": ""}
        try:
            if o.ist_verkauf:
                resp = client.close_demo_position(o.position_id, o.instrument_id, o.units)
                oid = (resp.get("orderForClose") or {}).get("orderID") or resp.get("orderId")
                row["OrderID"] = oid
                sid, msg = _poll(lambda: client.demo_close_status(oid), poll_timeout, poll_interval) if oid else (None, "")
            else:
                resp = client.open_demo_order(o.instrument_id, o.betrag)
                oid = resp.get("orderId") or (resp.get("orderForOpen") or {}).get("orderID")
                row["OrderID"] = oid
                sid, msg = _poll(lambda: client.demo_open_status(oid), poll_timeout, poll_interval) if oid else (None, "")
            row["Status"] = STATUS.get(sid, "Übermittelt (Status offen)" if sid is None else f"Status {sid}")
            row["Meldung"] = msg
        except BrokerError as exc:
            row["Status"], row["Meldung"] = "Fehler", str(exc)
            if str(exc) == WRITE_PERMISSION_HINT:  # alle weiteren Orders würden genauso scheitern
                abort["grund"] = "Abgebrochen: fehlende Schreibrechte (siehe erste Order)."
        results.append(row)
        time.sleep(pause)

    for i, o in enumerate(sells):
        run(o, i)
    if buys:  # Guthaben nach den Verkäufen neu lesen und Käufe ggf. kürzen
        try:
            credit = to_float(client.portfolio().get("credit")) or 0.0
            total = sum(o.betrag for o in buys)
            if total > credit > 0:
                for o in buys:
                    o.betrag *= credit / total * 0.99
        except BrokerError:
            pass
    for i, o in enumerate(buys, start=len(sells)):
        run(o, i)
    if progress:
        progress(1.0, "Fertig")
    df = pd.DataFrame(results)
    _log(plan, df)
    return df


def _log(plan: Plan, results: pd.DataFrame) -> None:
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        entry = {"zeit": datetime.now().isoformat(timespec="seconds"), "budget": plan.budget,
                 "orders": results.to_dict(orient="records")}
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
