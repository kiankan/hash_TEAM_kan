"""ساخت/تغییر رمز ادمین پنل: ADMIN_USER و ADMIN_PASS از محیط، یا پرسش تعاملی."""
import os
import re
import sys
from getpass import getpass

from werkzeug.security import generate_password_hash

from . import db

user = os.getenv("ADMIN_USER") or input("Username: ").strip()
pw = os.getenv("ADMIN_PASS") or getpass("Password (min 12 chars): ")
if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", user) or len(pw) < 12:
    sys.exit("نام کاربری نامعتبر یا رمز کوتاه‌تر از ۱۲ کاراکتر است.")
db.execute("INSERT INTO admins (username,password_hash,created_at) VALUES (%s,%s,%s)"
           " ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash)",
           (user, generate_password_hash(pw), db.now()))
print(f"ادمین «{user}» ذخیره شد.")
