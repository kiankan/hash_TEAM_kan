"""Nobitex market-price client.

Public market stats do not require an API key. If NOBITEX_API_KEY is present,
it is sent as an authentication header for endpoints that support it.
"""

import time
from decimal import Decimal, InvalidOperation
from threading import Lock

import requests

from . import config

PUBLIC_URL = "https://api.nobitex.ir/market/stats"
_CACHE_TTL = 30.0
_TIMEOUT = (5, 10)

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Hashbot/1.0",
    "Accept": "application/json",
})
if config.NOBITEX_API_KEY:
    _session.headers["Authorization"] = f"Token {config.NOBITEX_API_KEY}"

_cache: dict[str, tuple[float, Decimal | None]] = {}
_cache_lock = Lock()


def _latest(src: str, dst: str) -> Decimal | None:
    key = f"{src.lower()}-{dst.lower()}"

    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]

    price = None
    try:
        r = _session.post(
            PUBLIC_URL,
            data={"srcCurrency": src.lower(), "dstCurrency": dst.lower()},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        latest = (r.json().get("stats", {}).get(key) or {}).get("latest")
        if latest not in (None, "", "-"):
            value = Decimal(str(latest))
            if value.is_finite() and value > 0:
                price = value
    except (requests.RequestException, ValueError, InvalidOperation):
        price = None

    with _cache_lock:
        _cache[key] = (time.monotonic(), price)
    return price


def ton_price() -> Decimal | None:
    return _latest("ton", "rls")


def gram_price() -> Decimal | None:
    """Returns GRAM market price when Nobitex exposes the pair."""
    return _latest("gram", "rls")


def usdt_price() -> Decimal | None:
    return _latest("usdt", "rls")


def btc_price() -> Decimal | None:
    return _latest("btc", "rls")


def eth_price() -> Decimal | None:
    return _latest("eth", "rls")


def irt_rate(currency: str) -> Decimal | None:
    c = (currency or "").strip().lower()
    if c in ("irt", "toman", "tmn"):
        return Decimal(1)
    if c in ("rls", "irr"):
        return Decimal("0.1")

    direct = _latest(c, "rls")
    if direct:
        return direct / 10

    via = _latest(c, "usdt")
    usdt = _latest("usdt", "rls")
    if via and usdt:
        return via * usdt / 10
    return None


def format_toman(value: Decimal | None) -> str:
    if value is None:
        return "قیمت در نوبیتکس پیدا نشد"
    return f"{int(value):,} تومان"
