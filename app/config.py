import os
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x.lstrip("-").isdigit()}
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = _ids(os.getenv("ADMIN_TELEGRAM_IDS", ""))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tehran"))
SECRET_KEY = os.getenv("SECRET_KEY", "")
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", BASE_DIR / "backups"))
NOBITEX_API_KEY = os.getenv("NOBITEX_API_KEY", "").strip()
DB = dict(host=os.getenv("DB_HOST", "127.0.0.1"), user=os.getenv("DB_USER", "hashbot"), password=os.getenv("DB_PASSWORD", ""), database=os.getenv("DB_NAME", "hashbot"))
