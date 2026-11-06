"""eToro Public API (nur lesend).

Basis: https://public-api.etoro.com, Header ``x-api-key`` (ETORO_API_KEY), ``x-user-key`` (ETORO_USER_KEY)
und je Anfrage eine neue ``x-request-id`` (UUID). Limits: 60 Portfolio-Abfragen/Min., 120 Marktdaten/Min.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from .base import AccountSummary, BrokerError, Holding, HttpClient, env, to_float

WRITE_PERMISSION_HINT = (
    "eToro lehnt die Order ab (HTTP 403): Der Schlüssel hat keine Schreibrechte für das Demokonto. "
    "In eToro unter Einstellungen → Trading → API Key Management einen Schlüssel mit Umgebung „Demo“ und "
    "Berechtigung „Write“ anlegen und den User-Key als ETORO_API_WRITE in .env eintragen.")

TYPE_LABEL = {"Stocks": "Aktie", "ETF": "ETF", "Crypto": "Krypto", "Commodity": "Rohstoff", "Indices": "Index",
              "Forex": "Devisen", "Bonds": "Anleihe", "CFD": "CFD"}


class EToroClient(HttpClient):
    BROKER = "eToro"

    def __init__(self, api_key: str, user_key: str, account: str = "real", write_user_key: str | None = None, **kw):
        if account not in ("real", "demo"):
            raise ValueError("account muss 'real' oder 'demo' sein")
        super().__init__("https://public-api.etoro.com", **kw)
        self._api_key, self._user_key = api_key, user_key
        # User-Key mit Schreibrecht (Demo) – nur für Order-POSTs; er darf keine Marktdaten abrufen.
        self._write_user_key = write_user_key
        self._writing = False
        self.account = account

    @classmethod
    def from_env(cls, account: str = "real") -> "EToroClient | None":
        """Lesen immer mit ETORO_API_KEY/ETORO_USER_KEY. Im Demokonto werden Orders mit dem User-Key
        ETORO_API_WRITE (Umgebung Demo, Berechtigung Write) gesendet, falls vorhanden.
        Alternativ: ETORO_DEMO_API_KEY/ETORO_DEMO_USER_KEY als vollständiges Demo-Schlüsselpaar."""
        k, u = env("ETORO_API_KEY"), env("ETORO_USER_KEY")
        w = None
        if account == "demo":
            if env("ETORO_DEMO_API_KEY") and env("ETORO_DEMO_USER_KEY"):
                k, u = env("ETORO_DEMO_API_KEY"), env("ETORO_DEMO_USER_KEY")
            w = env("ETORO_API_WRITE")
        return cls(k, u, account, write_user_key=w) if k and u else None

    @property
    def konto(self) -> str:
        return "Echtgeld" if self.account == "real" else "Demo"

    def _headers(self):
        user = self._write_user_key if self._writing and self._write_user_key else self._user_key
        return {"x-api-key": self._api_key, "x-user-key": user,
                "x-request-id": str(uuid.uuid4()), "Accept": "application/json"}

    # --- Rohendpunkte -------------------------------------------------------
    def portfolio(self) -> dict:
        path = "/api/v1/trading/info/portfolio" if self.account == "real" else "/api/v1/trading/info/demo/portfolio"
        return (self.get(path) or {}).get("clientPortfolio", {})

    def pnl(self) -> dict:
        return (self.get(f"/api/v1/trading/info/{self.account}/pnl") or {}).get("clientPortfolio", {})

    def instruments(self, ids: list[int] | None = None, symbols: list[str] | None = None) -> dict[int, dict]:
        out: dict[int, dict] = {}
        keys = ids if ids else symbols or []
        for i in range(0, len(keys), 100):
            chunk = keys[i:i + 100]
            params = {"instrumentsIds" if ids else "symbols": ",".join(map(str, chunk)), "pageSize": 100}
            data = self.get("/api/v2/market-data/instruments", params) or {}
            for r in data.get("results", []):
                out[int(r["instrumentId"])] = r
        return out

    def rates(self, ids: list[int]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for i in range(0, len(ids), 1000):
            data = self.get("/api/v2/market-data/rates", {"instrumentIds": ",".join(map(str, ids[i:i + 1000]))}) or {}
            out.update({int(r["instrumentId"]): r for r in data.get("results", [])})
        return out

    def trade_history(self, days: int = 360, page_size: int = 100) -> list[dict]:
        if self.account != "real":
            raise BrokerError("Handelshistorie ist hier nur für das Echtgeld-Konto umgesetzt")
        min_date = (date.today() - timedelta(days=min(days, 364))).isoformat()
        data = self.get("/api/v1/trading/info/trade/history", {"minDate": min_date, "pageSize": page_size})
        return data if isinstance(data, list) else []

    def resolve_symbols(self, symbols: list[str]) -> dict[str, dict]:
        """Yahoo-/eToro-Symbol -> Instrument. Bei mehreren Börsenplätzen gewinnt die Hauptnotierung
        (kleinste instrumentId). Nicht handelbare Symbole fehlen im Ergebnis."""
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 50):
            chunk = [s.upper() for s in symbols[i:i + 50]]
            try:
                data = self.get("/api/v2/market-data/instruments", {"symbols": ",".join(chunk), "pageSize": 100})
            except BrokerError as exc:
                if "404" in str(exc):  # keines der Symbole gefunden
                    continue
                raise
            for r in data.get("results", []):
                sym = str(r.get("symbol", "")).upper()
                if sym in chunk and (sym not in out or r["instrumentId"] < out[sym]["instrumentId"]):
                    out[sym] = r
        return out

    # --- Orders: ausschließlich Demokonto -------------------------------------
    def _require_demo(self) -> None:
        if self.account != "demo":
            raise BrokerError("Orders sind nur im eToro-Demokonto freigeschaltet.")

    def _post_order(self, path: str, body: dict) -> dict:
        self._writing = True
        try:
            return self.post(path, body) or {}
        except BrokerError as exc:
            if "HTTP 403" in str(exc):
                raise BrokerError(WRITE_PERMISSION_HINT) from None
            raise
        finally:
            self._writing = False

    def open_demo_order(self, instrument_id: int, amount_usd: float) -> dict:
        """Market-Kauf (Long, ohne Hebel) über einen USD-Betrag. 200 = angenommen, nicht ausgeführt."""
        self._require_demo()
        body = {"action": "open", "transaction": "buy", "instrumentId": int(instrument_id), "orderType": "mkt",
                "amount": round(float(amount_usd), 2), "orderCurrency": "usd", "leverage": 1,
                "stopLossType": "fixed"}
        return self._post_order("/api/v2/trading/execution/demo/orders", body)

    def close_demo_position(self, position_id: int, instrument_id: int, units: float | None = None) -> dict:
        """Schließt eine Demo-Position ganz (units=None) oder teilweise."""
        self._require_demo()
        body = {"InstrumentID": int(instrument_id), "UnitsToDeduct": None if units is None else round(units, 6)}
        return self._post_order(f"/api/v1/trading/execution/demo/market-close-orders/positions/{int(position_id)}",
                                body)

    def demo_open_status(self, order_id: int) -> dict:
        self._require_demo()
        return self.get("/api/v2/trading/info/demo/orders:lookup", {"orderId": int(order_id)}) or {}

    def demo_close_status(self, order_id: int) -> dict:
        self._require_demo()
        return self.get(f"/api/v1/trading/info/demo/close-orders/{int(order_id)}") or {}

    # --- Aufbereitung ---------------------------------------------------------
    def holdings(self) -> tuple[AccountSummary, list[Holding]]:
        cp = self.pnl()
        positions = list(cp.get("positions") or [])
        for m in cp.get("mirrors") or []:  # CopyTrader-Positionen mitzählen
            positions += m.get("positions") or []
        summary = AccountSummary(self.BROKER, self.konto, "USD", cash=to_float(cp.get("credit")),
                                 guv=to_float(cp.get("unrealizedPnL")))
        if cp.get("mirrors"):
            summary.hinweise.append(f"{len(cp['mirrors'])} CopyTrader-Spiegelung(en) enthalten.")

        groups: dict[tuple[int, bool], list[dict]] = defaultdict(list)
        for p in positions:
            iid = _first(p, "instrumentID", "instrumentId")
            if iid is not None:
                groups[(int(iid), bool(p.get("isBuy", True)))].append(p)
        ids = sorted({k[0] for k in groups})
        meta, rates = {}, {}
        if ids:
            try:
                meta = self.instruments(ids=ids)
            except BrokerError as exc:
                summary.hinweise.append(f"Instrumentdaten nicht verfügbar ({exc}).")
            try:
                rates = self.rates(ids)
            except BrokerError:
                pass

        items = []
        for (iid, is_buy), ps in groups.items():
            m = meta.get(iid, {})
            units = sum(to_float(p.get("units")) or 0 for p in ps)
            invested = sum(to_float(p.get("amount")) or 0 for p in ps)
            open_val = sum((to_float(p.get("openRate")) or 0) * (to_float(p.get("units")) or 0) for p in ps)
            pnl = 0.0
            has_pnl = False
            for p in ps:
                u = p.get("unrealizedPnL")
                v = to_float(u.get("pnL")) if isinstance(u, dict) else to_float(p.get("pnL"))
                if v is not None:
                    pnl += v
                    has_pnl = True
            r = rates.get(iid, {})
            px = to_float(r.get("bid") if is_buy else r.get("ask"))
            lev = to_float(ps[0].get("leverage"))
            sym = m.get("symbol") or str(iid)
            cls_ = TYPE_LABEL.get(m.get("type", ""), m.get("type", ""))
            items.append(Holding(
                self.BROKER, self.konto, sym, name=m.get("displayName") or sym, anlageklasse=cls_, menge=units,
                einstandskurs=open_val / units if units else None, kurs=px,
                investiert=invested, guv=pnl if has_pnl else None,
                wert=invested + pnl if has_pnl else None, waehrung="USD", hebel=lev,
                richtung="Long" if is_buy else "Short", yahoo=yahoo_symbol(sym, m.get("type", "")),
            ))
        summary.positionen = len(items)
        summary.investiert = sum(h.investiert or 0 for h in items)
        if not items:
            summary.hinweise.append("Keine offenen Positionen.")
        return summary, items


def yahoo_symbol(symbol: str, etoro_type: str) -> str | None:
    """eToro-Symbole entsprechen bei Aktien/ETFs meist dem Yahoo-Format (z. B. AAPL, BAYN.DE, RR.L)."""
    if not symbol:
        return None
    if etoro_type == "Crypto":
        return f"{symbol.upper()}-USD"
    if etoro_type in ("Stocks", "ETF"):
        return symbol.upper()
    return None


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None
