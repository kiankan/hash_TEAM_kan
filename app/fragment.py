"""استخراج اطلاعات از صفحهٔ عمومی fragment.com (تا حد امکان؛ ساختار صفحه ممکن است تغییر کند)."""
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
NAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
STATUS_WORDS = ("Available", "Unavailable", "On auction", "For sale", "Sold", "Taken", "Auction ended")


def parse_target(text: str) -> str | None:
    t = (text or "").strip()
    if NAME_RE.match(t.lstrip("@")):
        return f"https://fragment.com/username/{t.lstrip('@')}"
    u = urlparse(t if "://" in t else "https://" + t)
    if u.scheme == "https" and u.hostname in ("fragment.com", "www.fragment.com") and len(u.path) > 1:
        return f"https://fragment.com{u.path}"
    return None


def fetch(url: str) -> dict:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    def meta(prop):
        m = soup.find("meta", property=prop)
        return m.get("content") if m else None

    info = {"url": url, "title": meta("og:title") or (soup.title.string if soup.title else None),
            "desc": meta("og:description"), "extra": {}}
    st = soup.select_one("[class*=section-header-status]")
    info["status"] = st.get_text(" ", strip=True) if st else None
    text = soup.get_text("\n", strip=True)
    if not info["status"]:
        info["status"] = next((w for w in STATUS_WORDS if w.lower() in text.lower()), None)
    vals = [v.get_text(" ", strip=True) for v in soup.select("[class*=tm-value]")]
    info["price"] = next((v for v in vals if re.search(r"\d", v)), None)
    for lab in soup.select("[class*=table-cell-label]"):
        val = lab.find_next_sibling()
        k, v = lab.get_text(" ", strip=True), (val.get_text(" ", strip=True) if val else "")
        if k and v:
            info["extra"][k[:40]] = v[:120]
    return info
