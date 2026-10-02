#!/usr/bin/env bash
# مدیریت Hashbot: sudo hashbot <دستور>
set -Eeuo pipefail
APP_DIR=/opt/hashbot
APP_USER=hashbot
SERVICES=(hashbot-bot hashbot-web)
[[ -f $APP_DIR/.env ]] && { set -a; . "$APP_DIR/.env"; set +a; }

need_root() { [[ $EUID -eq 0 ]] || { echo "با sudo اجرا کنید."; exit 1; }; }
as_app() { if [[ $EUID -eq 0 ]]; then runuser -u "$APP_USER" -- "$@"; else "$@"; fi; }

case "${1:-help}" in
  status)  systemctl --no-pager status "${SERVICES[@]}" | grep -E '●|Active:' ;;
  start|stop|restart) need_root; systemctl "$1" "${SERVICES[@]}"; echo "✅ $1" ;;
  logs)    journalctl -u "hashbot-${2:-bot}" -n 100 -f ;;
  update)
    need_root
    as_app git -C "$APP_DIR" pull --ff-only
    as_app "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
    mysql "$DB_NAME" < "$APP_DIR/schema.sql"
    chmod +x "$APP_DIR/manage.sh"
    systemctl restart "${SERVICES[@]}"
    echo "✅ به‌روزرسانی شد." ;;
  backup)
    mkdir -p "$APP_DIR/backups"
    f="$APP_DIR/backups/backup_$(date +%Y%m%d_%H%M%S).sql.gz"
    ( umask 077; MYSQL_PWD="$DB_PASSWORD" mysqldump -h "$DB_HOST" -u "$DB_USER" --single-transaction \
        --no-tablespaces "$DB_NAME" | gzip > "$f" )
    [[ $EUID -eq 0 ]] && chown "$APP_USER:$APP_USER" "$f"
    ls -1t "$APP_DIR"/backups/backup_*.sql.gz | tail -n +15 | xargs -r rm --
    echo "✅ $f" ;;
  backups) ls -lht "$APP_DIR"/backups/backup_*.sql.gz 2>/dev/null || echo "بکاپی نیست." ;;
  restore)
    need_root
    f=${2:?مسیر فایل .sql.gz را بدهید}
    [[ -f $f ]] || { echo "فایل پیدا نشد."; exit 1; }
    read -rp "⚠️ دیتابیس فعلی جایگزین می‌شود. ادامه؟ (yes): " a; [[ $a == yes ]] || exit 1
    systemctl stop "${SERVICES[@]}"
    gunzip -c "$f" | mysql "$DB_NAME"
    systemctl start "${SERVICES[@]}"; echo "✅ بازیابی شد." ;;
  passwd)
    need_root
    read -rp "نام کاربری: " u; read -rsp "رمز جدید (حداقل ۱۲): " p; echo
    (cd "$APP_DIR" && runuser -u "$APP_USER" -- env ADMIN_USER="$u" ADMIN_PASS="$p" "$APP_DIR/venv/bin/python" -m app.create_admin) ;;
  *) cat <<HELP
دستورها: sudo hashbot <دستور>
  status | start | stop | restart
  logs [bot|web]      مشاهدهٔ لاگ زنده
  update              git pull + وابستگی‌ها + مایگریشن + ری‌استارت
  backup | backups    ساخت/فهرست بکاپ (خودکار: هر شب ۰۳:۰۰)
  restore FILE        بازیابی از بکاپ
  passwd              ساخت/تغییر رمز ادمین پنل
HELP
  ;;
esac
