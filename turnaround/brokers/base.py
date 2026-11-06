"""Gemeinsame Bausteine für Broker-Clients (nur lesend)."""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd
import requests

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env() -> None:
    """Lädt .env (falls vorhanden), ohne bereits gesetzte Umgebungsvariablen zu überschreiben."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=False)
    except ImportError:  # pragma: no cover - python-dotenv ist in requirements.txt
        pass


def env(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


class BrokerError(RuntimeError):
    """Fehler beim Broker-Zugriff. Enthält nie API-Schlüssel."""


@dataclass
class Holding:
    broker: str
    konto: str
    symbol: str
    name: str = ""
    anlageklasse: str = ""
    menge: float = 0.0
    einstandskurs: float | None = None
    kurs: float | None = None
    wert: float | None = None
    investiert: float | None = None
    guv: float | None = None
    waehrung: str = ""
    hebel: float | None = None
    richtung: str = "Long"
    yahoo: str | None = None  # Ticker für den Abgleich mit dem Turnaround-Scanner


@dataclass
class AccountSummary:
    broker: str
    konto: str
    waehrung: str
    cash: float | None = None
    investiert: float | None = None
    guv: float | None = None
    positionen: int = 0
    hinweise: list[str] = field(default_factory=list)


def holdings_frame(items: list[Holding]) -> pd.DataFrame:
    cols = list(Holding.__dataclass_fields__)
    return pd.DataFrame([asdict(h) for h in items], columns=cols)


class HttpClient:
    """Dünne requests-Hülle mit Timeout, 429-Behandlung und schlüsselfreien Fehlermeldungen."""

    def __init__(self, base_url: str, timeout: float = 20.0, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:  # in Unterklassen überschreiben
        return {}

    def get(self, path: str, params: dict | None = None):
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                r = self.session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                raise BrokerError(f"Verbindung zu {self.base_url} fehlgeschlagen: {type(exc).__name__}") from None
            if r.status_code == 429 and attempt < self.max_retries:
                wait = float(r.headers.get("Retry-After", "2") or 2)
                time.sleep(min(wait, 10))
                continue
            if r.status_code in (200, 206):
                try:
                    return r.json()
                except ValueError:
                    raise BrokerError(f"Ungültige Antwort von {path}") from None
            raise BrokerError(f"{path}: HTTP {r.status_code} – {_error_text(r)}")
        raise BrokerError(f"{path}: Rate-Limit überschritten")


def _error_text(r: requests.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:150]
    if isinstance(body, dict):
        if isinstance(body.get("errors"), list) and body["errors"]:
            e = body["errors"][0]
            return str(e.get("title") or e.get("code") or e)[:150]
        return str(body.get("errorMessage") or body.get("errorCode") or body.get("message") or body)[:150]
    return str(body)[:150]


def to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
