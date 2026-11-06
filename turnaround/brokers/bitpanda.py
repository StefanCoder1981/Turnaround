"""Bitpanda: Fusion-API (Krypto-Handel) und optional die klassische Public API (Gesamtportfolio).

Fusion:   https://api.fusion.bitpanda.com, Header ``x-api-key`` (Umgebungsvariable FUSION_API_KEY)
Klassisch: https://api.public.bitpanda.com, Header ``x-api-key`` (Umgebungsvariable BITPANDA_API_KEY)
Der Fusion-Schlüssel gilt nicht für die klassische API – dafür ist ein eigener Schlüssel nötig.
"""
from __future__ import annotations

from .base import AccountSummary, BrokerError, Holding, HttpClient, env, to_float

FIAT = {"EUR", "USD", "CHF", "GBP", "PLN", "CZK", "HUF", "SEK", "DKK", "TRY"}


class FusionClient(HttpClient):
    BROKER = "Bitpanda"
    KONTO = "Fusion"

    def __init__(self, api_key: str, quote: str = "EUR", **kw):
        super().__init__("https://api.fusion.bitpanda.com", **kw)
        self._key = api_key
        self.quote = quote

    @classmethod
    def from_env(cls) -> "FusionClient | None":
        key = env("FUSION_API_KEY")
        return cls(key) if key else None

    def _headers(self):
        return {"x-api-key": self._key, "Accept": "application/json"}

    # --- Rohendpunkte -------------------------------------------------------
    def account(self) -> dict:
        return self.get("/v1/account")

    def balances(self) -> list[dict]:
        data = self.get("/v1/account/balances")
        if isinstance(data, dict):
            data = data.get("data") or data.get("balances") or []
        return data or []

    def tickers(self, pairs: list[str] | None = None) -> dict[str, dict]:
        params = {"pair": ",".join(pairs)} if pairs else None
        return {t["pair"]: t for t in self.get("/v1/tickers", params) or [] if "pair" in t}

    def trades(self, pair: str | None = None, limit: int = 100) -> list[dict]:
        params = {"limit": limit, **({"pair": pair} if pair else {})}
        return (self.get("/v1/account/trades", params) or {}).get("data", [])

    def orders(self, status: str = "open", limit: int = 100) -> list[dict]:
        return (self.get("/v1/account/orders", {"status": status, "limit": limit}) or {}).get("data", [])

    # --- Aufbereitung ---------------------------------------------------------
    def holdings(self) -> tuple[AccountSummary, list[Holding]]:
        raw = self.balances()
        summary = AccountSummary(self.BROKER, self.KONTO, self.quote)
        coins: list[tuple[str, float]] = []
        cash = 0.0
        for b in raw:
            sym = str(_first(b, "symbol", "asset", "currency", "coin", "assetSymbol") or "").upper()
            total = to_float(_first(b, "total", "balance", "amount"))
            if total is None:
                total = (to_float(_first(b, "available", "free")) or 0.0) + (to_float(_first(b, "locked", "reserved")) or 0.0)
            if not sym or not total:
                continue
            if sym == self.quote:
                cash += total
            elif sym not in FIAT:
                coins.append((sym, total))
        prices: dict[str, dict] = {}
        if coins:
            try:
                prices = self.tickers([f"{s}-{self.quote}" for s, _ in coins])
            except BrokerError:
                prices = self.tickers()  # Fallback: alle Paare, ungültige Paare werden ignoriert
        items = []
        for sym, qty in coins:
            pair = f"{sym}-{self.quote}"
            px = to_float((prices.get(pair) or {}).get("price"))
            items.append(Holding(self.BROKER, self.KONTO, sym, name=sym, anlageklasse="Krypto", menge=qty,
                                 kurs=px, wert=qty * px if px else None, waehrung=self.quote, yahoo=pair))
        summary.cash = cash
        summary.positionen = len(items)
        summary.investiert = sum(h.wert or 0 for h in items)
        if not raw:
            summary.hinweise.append("Keine Bestände im Fusion-Konto.")
        return summary, items


class BitpandaClassicClient(HttpClient):
    """Klassische Bitpanda Public API – Gesamtportfolio inkl. Aktien/ETFs/Metalle (sofern freigeschaltet)."""
    BROKER = "Bitpanda"
    KONTO = "Hauptkonto"

    def __init__(self, api_key: str, **kw):
        super().__init__("https://api.public.bitpanda.com", **kw)
        self._key = api_key

    @classmethod
    def from_env(cls) -> "BitpandaClassicClient | None":
        key = env("BITPANDA_API_KEY")
        return cls(key) if key else None

    def _headers(self):
        return {"x-api-key": self._key, "Accept": "application/json"}

    def portfolio(self) -> list[dict]:
        return (self.get("/v1/portfolio") or {}).get("data", [])

    def assets(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            data = self.get("/v1/assets", {"id": ",".join(chunk), "page_size": len(chunk)}) or {}
            out.update({a["id"]: a for a in data.get("data", []) if "id" in a})
        return out

    def holdings(self) -> tuple[AccountSummary, list[Holding]]:
        rows = self.portfolio()
        ids = sorted({r.get("asset_id") for r in rows if r.get("asset_id")} |
                     {(r.get("currency_balance") or {}).get("currency_id") for r in rows} - {None})
        meta = self.assets(ids) if ids else {}
        items = []
        for r in rows:
            a = meta.get(r.get("asset_id"), {})
            qty = to_float(_val(r.get("balance"))) or 0.0
            if not qty:
                continue
            ccy = meta.get((r.get("currency_balance") or {}).get("currency_id"), {}).get("symbol", "EUR")
            cls_ = _asset_class(a)
            sym = a.get("symbol") or ""
            items.append(Holding(
                self.BROKER, self.KONTO, sym, name=a.get("name") or sym, anlageklasse=cls_, menge=qty,
                einstandskurs=to_float(_val(r.get("average_buy_price"))),
                wert=to_float(_val(r.get("currency_balance"))), investiert=to_float(_val(r.get("invested_amount"))),
                guv=to_float(_val(r.get("total_return"))), waehrung=ccy,
                yahoo=f"{sym}-EUR" if cls_ == "Krypto" and sym else None,
            ))
            if items[-1].wert and qty:
                items[-1].kurs = items[-1].wert / qty
        s = AccountSummary(self.BROKER, self.KONTO, "EUR", positionen=len(items),
                           investiert=sum(h.investiert or 0 for h in items), guv=sum(h.guv or 0 for h in items))
        return s, items


def _first(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _val(o):
    return o.get("value") if isinstance(o, dict) else o


def _asset_class(a: dict) -> str:
    t = f"{a.get('type', '')} {a.get('group', '')}".upper()
    for key, label in (("CRYPTO", "Krypto"), ("METAL", "Edelmetall"), ("COMMODITY", "Edelmetall"),
                       ("ETF", "ETF"), ("STOCK", "Aktie"), ("SECURITY", "Aktie"), ("INDEX", "Index")):
        if key in t:
            return label
    return t.strip().title() or "Sonstiges"
