#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/hashbot}"
SERVICE_BOT="hashbot-bot"
SERVICE_WEB="hashbot-web"
BACKUP_ROOT="${APP_DIR}/upgrade-backups"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok(){ echo -e "${GREEN}$*${NC}"; }
msg(){ echo -e "${CYAN}$*${NC}"; }
warn(){ echo -e "${YELLOW}$*${NC}"; }
err(){ echo -e "${RED}$*${NC}"; }

[[ $EUID -eq 0 ]] || { err "با root اجرا کن: sudo bash update_bot_only.sh"; exit 1; }
[[ -d "$APP_DIR/.git" ]] || { err "ریپو در $APP_DIR پیدا نشد."; exit 1; }

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/${STAMP}"
mkdir -p "$BACKUP_DIR"

msg "1) توقف سرویس‌ها و گرفتن بکاپ..."
systemctl stop "$SERVICE_BOT" 2>/dev/null || true
systemctl stop "$SERVICE_WEB" 2>/dev/null || true

# Backup only web-related files/configs before deletion.
mkdir -p "$BACKUP_DIR/app"
for f in "$APP_DIR/app/web.py" "$APP_DIR/app/create_admin.py"; do
    [[ -f "$f" ]] && cp -a "$f" "$BACKUP_DIR/app/"
done
[[ -d "$APP_DIR/app/templates" ]] && cp -a "$APP_DIR/app/templates" "$BACKUP_DIR/app/"
[[ -f "$APP_DIR/manage.sh" ]] && cp -a "$APP_DIR/manage.sh" "$BACKUP_DIR/"
[[ -f "$APP_DIR/requirements.txt" ]] && cp -a "$APP_DIR/requirements.txt" "$BACKUP_DIR/"
[[ -f "$APP_DIR/install.sh" ]] && cp -a "$APP_DIR/install.sh" "$BACKUP_DIR/"

ok "بکاپ تغییرات در: $BACKUP_DIR"

msg "2) دریافت آخرین کد ریپو..."
git -C "$APP_DIR" fetch origin
git -C "$APP_DIR" pull --ff-only origin main

msg "3) حذف کامل بخش Web..."
rm -f "$APP_DIR/app/web.py" "$APP_DIR/app/create_admin.py"
rm -rf "$APP_DIR/app/templates"

# Remove old web service and common nginx config if present.
systemctl disable --now "$SERVICE_WEB" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_WEB}.service"
rm -f /etc/nginx/sites-enabled/hashbot /etc/nginx/sites-available/hashbot 2>/dev/null || true
systemctl daemon-reload

msg "4) پاکسازی requirements.txt..."
cat > "$APP_DIR/requirements.txt" <<'EOF'
python-telegram-bot[job-queue]>=21,<23
PyMySQL>=1.1,<2
cryptography>=42
python-dotenv>=1.0
requests>=2.31
beautifulsoup4>=4.12
jdatetime>=5
EOF

msg "5) تبدیل manage.sh به Bot-only..."
cat > "$APP_DIR/manage.sh" <<'EOF'
#!/usr/bin/env bash
# مدیریت HashBot: sudo hashbot <دستور>
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/hashbot}"
APP_USER="${APP_USER:-hashbot}"
SERVICE="hashbot-bot"

[[ -f "$APP_DIR/.env" ]] && { set -a; . "$APP_DIR/.env"; set +a; }

need_root() { [[ $EUID -eq 0 ]] || { echo "با sudo اجرا کنید."; exit 1; }; }

case "${1:-help}" in
  status)
    systemctl --no-pager --full status "$SERVICE" || true
    ;;
  start|stop|restart)
    need_root
    systemctl "$1" "$SERVICE"
    echo "✅ $1"
    ;;
  logs)
    journalctl -u "$SERVICE" -n 100 -f
    ;;
  update)
    need_root
    git -C "$APP_DIR" pull --ff-only
    if [[ -x "$APP_DIR/.venv/bin/pip" ]]; then
      "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
    elif [[ -x "$APP_DIR/venv/bin/pip" ]]; then
      "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
    fi
    [[ -f "$APP_DIR/schema.sql" ]] && {
      set -a; . "$APP_DIR/.env"; set +a
      MYSQL_PWD="$DB_PASSWORD" mysql -h "$DB_HOST" -u "$DB_USER" "$DB_NAME" < "$APP_DIR/schema.sql"
    }
    systemctl daemon-reload
    systemctl restart "$SERVICE"
    echo "✅ به‌روزرسانی شد."
    ;;
  backup)
    need_root
    mkdir -p "$APP_DIR/backups"
    f="$APP_DIR/backups/backup_$(date +%Y%m%d_%H%M%S).sql.gz"
    ( umask 077; MYSQL_PWD="$DB_PASSWORD" mysqldump -h "$DB_HOST" -u "$DB_USER" --single-transaction "$DB_NAME" | gzip > "$f" )
    chown "$APP_USER:$APP_USER" "$f" 2>/dev/null || true
    ls -1t "$APP_DIR"/backups/backup_*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm --
    echo "✅ $f"
    ;;
  backups)
    ls -lht "$APP_DIR"/backups/backup_*.sql.gz 2>/dev/null || echo "بکاپی نیست."
    ;;
  restore)
    need_root
    f="${2:?مسیر فایل .sql.gz را بدهید}"
    [[ -f "$f" ]] || { echo "فایل پیدا نشد."; exit 1; }
    read -rp "⚠️ دیتابیس فعلی جایگزین می‌شود. ادامه؟ (yes): " a
    [[ "$a" == "yes" ]] || exit 1
    systemctl stop "$SERVICE"
    gunzip -c "$f" | MYSQL_PWD="$DB_PASSWORD" mysql -h "$DB_HOST" -u "$DB_USER" "$DB_NAME"
    systemctl start "$SERVICE"
    echo "✅ بازیابی شد."
    ;;
  *)
    cat <<HELP
دستورها: sudo hashbot <دستور>

  status              وضعیت Bot
  start               شروع Bot
  stop                توقف Bot
  restart             ری‌استارت Bot
  logs                لاگ زنده Bot
  update              دریافت آخرین کد + وابستگی‌ها + ری‌استارت
  backup              ساخت بکاپ دیتابیس
  backups             فهرست بکاپ‌ها
  restore FILE        بازیابی بکاپ

این نسخه کاملاً Bot-only است و Web Panel ندارد.
HELP
    ;;
esac
EOF
chmod +x "$APP_DIR/manage.sh"

msg "6) حذف سرویس وب و نصب وابستگی‌های جدید..."
systemctl disable --now "$SERVICE_WEB" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_WEB}.service"
systemctl daemon-reload

if [[ -x "$APP_DIR/.venv/bin/pip" ]]; then
    "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
elif [[ -x "$APP_DIR/venv/bin/pip" ]]; then
    "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
else
    python3 -m venv "$APP_DIR/.venv"
    "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
    "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
fi

msg "7) فعال‌سازی سرویس Bot..."
systemctl enable "$SERVICE_BOT"
systemctl restart "$SERVICE_BOT"

msg "8) پاکسازی کش Python..."
find "$APP_DIR/app" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

ok "=========================================="
ok "✅ تبدیل به Bot-only با موفقیت انجام شد."
ok "=========================================="
echo
echo "Web files removed:"
echo "  app/web.py"
echo "  app/create_admin.py"
echo "  app/templates/"
echo
echo "Backup:"
echo "  $BACKUP_DIR"
echo
echo "وضعیت Bot:"
systemctl --no-pager --full status "$SERVICE_BOT" | sed -n '1,14p' || true
