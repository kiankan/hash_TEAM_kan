"""Nobitex client + bot-managed credentials.

Credentials are stored outside the database so schema.sql does not need migration.
The file is chmod 600 and contains no Telegram/UI data.
"""
import json
import os
import time
from decimal import Decimal
from pathlib import Path

import requests

from . import config

URL = "https://api.nobitex.ir/market/stats"
CRED_FILE = Path(os.getenv("NOBITEX_CREDENTIAL_FILE", str(config.BASE_DIR / ".nobitex_credentials.json")))
_session = requests.Session()
_cache = {}


def get_credentials():
    try:
        if not CRED_FILE.exists():
            return "", ""
        data = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        return str(data.get("api_key", "")).strip(), str(data.get("secret_key", "")).strip()
    except Exception:
        return "", ""


def save_credentials(api_key, secret_key):
    CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CRED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"api_key": api_key, "secret_key": secret_key}), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CRED_FILE)
    os.chmod(CRED_FILE, 0o600)
    _session.headers.pop("Authorization", None)
    if api_key:
        _session.headers["Authorization"] = f"Token {api_key}"


def clear_credentials():
    try:
        CRED_FILE.unlink(missing_ok=True)
    finally:
        _session.headers.pop("Authorization", None)
        _cache.clear()


def credentials_status():
    k, s = get_credentials()
    return {"key": bool(k), "secret": bool(s)}


def _latest(src, dst):
    key = f"{src.lower()}-{dst.lower()}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    price = None
    try:
        k, _ = get_credentials()
        headers = {"Authorization": f"Token {k}"} if k else {}
        r = _session.post(
            URL,
            data={"srcCurrency": src.lower(), "dstCurrency": dst.lower()},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        st = r.json().get("stats", {}).get(key) or {}
        if st.get("latest") not in (None, "", "-"):
            price = Decimal(str(st["latest"]))
    except Exception:
        price = None
    _cache[key] = (time.time(), price)
    return price


def usd_price(currency):
    c = currency.strip().lower()
    if c in ("usdt", "usd"):
        return Decimal(1)
    return _latest(c, "usdt")


def irt_rate(currency):
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


def format_toman(v): return f"{v:,.0f}"
def format_usd(v): return f"{v:,.2f}"
