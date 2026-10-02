"""قیمت ارزها از API عمومی نوبیتکس (تومان)."""
import time
from decimal import Decimal

import requests

URL = "https://api.nobitex.ir/market/stats"
_cache: dict[str, tuple[float, Decimal | None]] = {}


def _latest(src: str, dst: str) -> Decimal | None:
    key = f"{src}-{dst}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    price = None
    try:
        r = requests.post(URL, data={"srcCurrency": src, "dstCurrency": dst}, timeout=10)
        st = r.json().get("stats", {}).get(key) or {}
        if st.get("latest") not in (None, "", "-"):
            price = Decimal(str(st["latest"]))
    except Exception:
        price = None
    _cache[key] = (time.time(), price)
    return price


def irt_rate(currency: str) -> Decimal | None:
    """قیمت یک واحد ارز به تومان؛ در صورت نبودن قیمت None."""
    c = currency.strip().lower()
    if c in ("irt", "toman", "tmn"):
        return Decimal(1)
    if c in ("rls", "irr"):
        return Decimal("0.1")
    direct = _latest(c, "rls")
    if direct:
        return direct / 10
    via, usdt = _latest(c, "usdt"), _latest("usdt", "rls")
    if via and usdt:
        return via * usdt / 10
    return None
