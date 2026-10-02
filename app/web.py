import gzip
import os
import re
import subprocess
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from . import config, db, nobitex, services

if not config.SECRET_KEY:
    raise RuntimeError("SECRET_KEY is not set")

P = "/admin"
app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=config.SECRET_KEY, SESSION_COOKIE_NAME="hb_session", SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2), WTF_CSRF_TIME_LIMIT=7200, MAX_CONTENT_LENGTH=64 * 1024)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["300 per hour"], storage_uri="memory://")
DUMMY_HASH = generate_password_hash("dummy-password-for-timing")
BACKUP_RE = re.compile(r"^backup_\d{8}_\d{6}\.sql\.gz$")


@app.after_request
def headers(r):
    r.headers.update({
        "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
    return r


app.jinja_env.filters["irt"] = lambda n: services.fmt(n) if n is not None else "—"
app.jinja_env.filters["jd"] = lambda d: services.jdate(d.date() if hasattr(d, "date") else d)


def login_required(fn):
    @wraps(fn)
    def w(*a, **k):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*a, **k)
    return w


def back(endpoint):
    return redirect(url_for(endpoint))


# ---------- ورود / خروج ----------
@app.route(P + "/login", methods=["GET", "POST"])
@limiter.limit("5 per minute;30 per hour", methods=["POST"])
def login():
    if request.method == "POST":
        u, p = request.form.get("username", "")[:64], request.form.get("password", "")[:256]
        row = db.one("SELECT * FROM admins WHERE username=%s", (u,))
        ok = check_password_hash(row["password_hash"] if row else DUMMY_HASH, p) and row is not None
        db.execute("INSERT INTO login_logs (username,ip,success,created_at) VALUES (%s,%s,%s,%s)",
                   (u, get_remote_address(), int(ok), db.now()))
        if ok:
            session.clear()
            session.permanent = True
            session["admin"] = u
            return back("dashboard")
        flash("نام کاربری یا رمز عبور اشتباه است.", "err")
    return render_template("login.html")


@app.post(P + "/logout")
def logout():
    session.clear()
    return back("login")


@app.errorhandler(429)
def too_many(e):
    return "تلاش‌های زیاد؛ کمی بعد دوباره امتحان کنید.", 429


# ---------- داشبورد ----------
@app.route(P + "/")
@login_required
def dashboard():
    t0 = db.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return render_template(
        "dashboard.html", s=services.summary(t0, t0 + timedelta(days=1)),
        tx=db.q("SELECT * FROM transactions ORDER BY id DESC LIMIT 5"),
        rem=db.one("SELECT COUNT(*) c FROM reminders WHERE active=1")["c"])


# ---------- تراکنش‌ها ----------
@app.route(P + "/transactions", methods=["GET", "POST"])
@login_required
def transactions():
    if request.method == "POST":
        f = request.form
        h, amt = services.normalize_hash(f.get("tx_hash", "")), services.parse_amount(f.get("amount", ""))
        net, cur, kind = f.get("network", "").strip().upper()[:32], f.get("currency", "").strip().upper()[:16], f.get("kind")
        if not (h and amt and net and cur and kind in ("income", "expense")):
            flash("ورودی نامعتبر است.", "err")
        else:
            tid, _ = services.add_tx(h, net, cur, amt, kind, f.get("description", "").strip()[:500])
            flash("ثبت شد." if tid else "این Hash قبلاً ثبت شده است.", "ok" if tid else "err")
        return back("transactions")
    s = request.args.get("q", "").strip()[:100]
    if s:
        like = f"%{s}%"
        rows = db.q("SELECT * FROM transactions WHERE tx_hash LIKE %s OR description LIKE %s OR currency=%s"
                    " ORDER BY id DESC LIMIT 200", (like, like, s.upper()))
    else:
        rows = db.q("SELECT * FROM transactions ORDER BY id DESC LIMIT 200")
    return render_template("transactions.html", rows=rows, q=s)


@app.post(P + "/transactions/<int:i>/delete")
@login_required
def tx_delete(i):
    db.execute("DELETE FROM transactions WHERE id=%s", (i,))
    flash("حذف شد.", "ok")
    return back("transactions")


# ---------- یادآوری‌ها ----------
@app.route(P + "/reminders", methods=["GET", "POST"])
@login_required
def reminders():
    if request.method == "POST":
        h, t = request.form.get("tx_hash", "").strip()[:191], request.form.get("title", "").strip()[:300]
        try:
            days = min(max(int(request.form.get("days", 7)), 1), 365)
        except ValueError:
            days = 0
        chat = services.default_chat_id()
        if not (t and days and chat):
            flash("ورودی نامعتبر است (یا هیچ کاربر مجازی تعریف نشده).", "err")
        else:
            services.create_reminder(chat, h, t, days)
            flash("یادآوری ثبت شد.", "ok")
        return back("reminders")
    return render_template("reminders.html", rows=db.q("SELECT * FROM reminders ORDER BY active DESC, next_at LIMIT 300"))


@app.post(P + "/reminders/<int:i>/toggle")
@login_required
def rem_toggle(i):
    db.execute("UPDATE reminders SET active=1-active, next_at=IF(active=0, %s, next_at) WHERE id=%s",
               (db.now() + timedelta(days=7), i))
    return back("reminders")


@app.post(P + "/reminders/<int:i>/delete")
@login_required
def rem_delete(i):
    db.execute("DELETE FROM reminders WHERE id=%s", (i,))
    return back("reminders")


# ---------- گزارش‌ها ----------
@app.route(P + "/reports")
@login_required
def reports():
    rows = db.q("""SELECT DATE(created_at) d,
        SUM(CASE WHEN kind='income' THEN COALESCE(value_irt,0) ELSE 0 END) income,
        SUM(CASE WHEN kind='expense' THEN COALESCE(value_irt,0) ELSE 0 END) expense,
        COUNT(*) cnt, SUM(value_irt IS NULL) unpriced
        FROM transactions WHERE created_at >= %s GROUP BY DATE(created_at) ORDER BY d DESC""",
                db.now() - timedelta(days=30))
    for r in rows:
        net = r["income"] - r["expense"]
        r["profit"], r["loss"] = max(net, 0), max(-net, 0)
    return render_template("reports.html", rows=rows)


# ---------- کاربران ----------
@app.route(P + "/users")
@login_required
def users():
    return render_template("users.html", allowed=db.q("SELECT * FROM allowed_users ORDER BY created_at"),
                           env_ids=sorted(config.ADMIN_IDS), admins=db.q("SELECT id,username,created_at FROM admins"))


@app.post(P + "/users/add")
@login_required
def user_add():
    tid, name = request.form.get("telegram_id", "").strip(), request.form.get("name", "").strip()[:100]
    if not re.fullmatch(r"\d{4,15}", tid):
        flash("Telegram ID نامعتبر است.", "err")
    else:
        db.execute("INSERT IGNORE INTO allowed_users (telegram_id,name,created_at) VALUES (%s,%s,%s)", (int(tid), name, db.now()))
        flash("به لیست مجاز اضافه شد.", "ok")
    return back("users")


@app.post(P + "/users/<int:tid>/delete")
@login_required
def user_delete(tid):
    db.execute("DELETE FROM allowed_users WHERE telegram_id=%s", (tid,))
    return back("users")


@app.post(P + "/admins/add")
@login_required
def admin_add():
    u, p = request.form.get("username", "").strip(), request.form.get("password", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", u) or len(p) < 12:
        flash("نام کاربری نامعتبر یا رمز کوتاه‌تر از ۱۲ کاراکتر است.", "err")
    else:
        db.execute("INSERT IGNORE INTO admins (username,password_hash,created_at) VALUES (%s,%s,%s)",
                   (u, generate_password_hash(p), db.now()))
        flash("ادمین پنل ساخته شد.", "ok")
    return back("users")


@app.post(P + "/admins/<int:i>/delete")
@login_required
def admin_delete(i):
    row = db.one("SELECT username FROM admins WHERE id=%s", (i,))
    if row and row["username"] != session["admin"]:
        db.execute("DELETE FROM admins WHERE id=%s", (i,))
    else:
        flash("حذف حساب خودتان ممکن نیست.", "err")
    return back("users")


# ---------- تنظیمات / لاگ‌ها / بکاپ ----------
@app.route(P + "/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        row = db.one("SELECT * FROM admins WHERE username=%s", (session["admin"],))
        cur, new = request.form.get("current", ""), request.form.get("new", "")
        if not row or not check_password_hash(row["password_hash"], cur):
            flash("رمز فعلی اشتباه است.", "err")
        elif len(new) < 12:
            flash("رمز جدید باید حداقل ۱۲ کاراکتر باشد.", "err")
        else:
            db.execute("UPDATE admins SET password_hash=%s WHERE id=%s", (generate_password_hash(new), row["id"]))
            flash("رمز تغییر کرد.", "ok")
        return back("settings")
    return render_template("settings.html", usdt=nobitex.irt_rate("usdt"))


@app.route(P + "/logs")
@login_required
def logs():
    return render_template("logs.html", logins=db.q("SELECT * FROM login_logs ORDER BY id DESC LIMIT 100"),
                           events=db.q("SELECT * FROM event_logs ORDER BY id DESC LIMIT 100"))


def _backups():
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return sorted((p for p in config.BACKUP_DIR.iterdir() if BACKUP_RE.match(p.name)), reverse=True)


@app.route(P + "/backup", methods=["GET", "POST"])
@login_required
def backup():
    if request.method == "POST":
        path = config.BACKUP_DIR / f"backup_{db.now():%Y%m%d_%H%M%S}.sql.gz"
        try:
            config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            p = subprocess.run(
                ["mysqldump", "-h", config.DB["host"], "-u", config.DB["user"], "--single-transaction",
                 config.DB["database"]],
                capture_output=True, timeout=120, env={**os.environ, "MYSQL_PWD": config.DB["password"]})
            if p.returncode:
                raise RuntimeError(p.stderr.decode()[:200])
            with gzip.open(path, "wb") as f:
                f.write(p.stdout)
            os.chmod(path, 0o600)
            flash("بکاپ ساخته شد.", "ok")
        except Exception as e:
            db.log_event("error", f"backup failed: {e}")
            flash("ساخت بکاپ ناموفق بود (جزئیات در لاگ‌ها).", "err")
        return back("backup")
    return render_template("backup.html", files=[(p.name, p.stat().st_size // 1024) for p in _backups()[:30]])


@app.route(P + "/backup/<name>")
@login_required
def backup_dl(name):
    if not BACKUP_RE.match(name):
        abort(404)
    return send_from_directory(config.BACKUP_DIR, name, as_attachment=True)
