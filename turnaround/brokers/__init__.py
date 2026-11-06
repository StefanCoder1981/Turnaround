"""Broker-Anbindungen (nur lesend): Bitpanda Fusion, Bitpanda klassisch (optional), eToro."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .base import AccountSummary, BrokerError, Holding, holdings_frame, load_env
from .bitpanda import BitpandaClassicClient, FusionClient
from .etoro import EToroClient

__all__ = ["AccountSummary", "BrokerError", "Holding", "BrokerStatus", "fetch_all", "configured", "load_env",
           "FusionClient", "BitpandaClassicClient", "EToroClient"]


@dataclass
class BrokerStatus:
    name: str
    konto: str
    status: str  # "verbunden" | "nicht konfiguriert" | "Fehler"
    meldung: str = ""
    summary: AccountSummary | None = None
    holdings: list[Holding] = field(default_factory=list)


def configured() -> dict[str, bool]:
    load_env()
    return {
        "Bitpanda Fusion": FusionClient.from_env() is not None,
        "Bitpanda Hauptkonto": BitpandaClassicClient.from_env() is not None,
        "eToro": EToroClient.from_env() is not None,
    }


def _fetch(name: str, konto: str, client, env_hint: str) -> BrokerStatus:
    if client is None:
        return BrokerStatus(name, konto, "nicht konfiguriert", f"{env_hint} in .env eintragen")
    try:
        s, items = client.holdings()
        return BrokerStatus(name, konto, "verbunden", "; ".join(s.hinweise), s, items)
    except BrokerError as exc:
        return BrokerStatus(name, konto, "Fehler", str(exc))


def fetch_all(etoro_account: str = "real") -> tuple[list[BrokerStatus], pd.DataFrame]:
    load_env()
    et = EToroClient.from_env(etoro_account)
    statuses = [
        _fetch("Bitpanda", "Fusion", FusionClient.from_env(), "FUSION_API_KEY"),
        _fetch("Bitpanda", "Hauptkonto", BitpandaClassicClient.from_env(), "BITPANDA_API_KEY"),
        _fetch("eToro", "Echtgeld" if etoro_account == "real" else "Demo", et, "ETORO_API_KEY und ETORO_USER_KEY"),
    ]
    items = [h for s in statuses for h in s.holdings]
    return statuses, holdings_frame(items)
