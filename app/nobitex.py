"""قیمت ارزها از نوبیتکس؛ بدون تغییر در API داخلی ربات."""
import time
from decimal import Decimal
import requests
from . import config
URL = "https://api.nobitex.ir/market/stats"
_session = requests.Session()
if config.NOBITEX_API_KEY:
    _session.headers.update({"Authorization": f"Token {config.NOBITEX_API_KEY}"})
_cache: dict[str, tuple[float, Decimal | None]] = {}

def _latest(src: str, dst: str) -> Decimal | None:
    key = f"{src.lower()}-{dst.lower()}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    price = None
    try:
        r = _session.post(URL, data={"srcCurrency": src.lower(), "dstCurrency": dst.lower()}, timeout=10)
        r.raise_for_status()
        st = r.json().get("stats", {}).get(key) or {}
        if st.get("latest") not in (None, "", "-"):
            price = Decimal(str(st["latest"]))
    except Exception:
        price = None
    _cache[key] = (time.time(), price)
    return price

def usd_price(currency: str) -> Decimal | None:
    c = currency.strip().lower()
    if c in ("usdt", "usd"):
        return Decimal(1)
    return _latest(c, "usdt")

def irt_rate(currency: str) -> Decimal | None:
    c = currency.strip().lower()
    if c in ("irt", "toman", "tmn"):
        return Decimal(1)
    if c in ("rls", "irr"):
        return Decimal("0.1")
    direct = _latest(c, "rls")
    if direct:
        return direct / 10
    via, usdt = usd_price(c), _latest("usdt", "rls")
    if via and usdt:
        return via * usdt / 10
    return None

def format_toman(v: Decimal) -> str:
    return f"{v:,.0f}"

def format_usd(v: Decimal) -> str:
    return f"{v:,.2f}"
