#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="hashbot-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${APP_DIR}/.env"
BACKUP_DIR="${APP_DIR}/backups"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

msg() { echo -e "${CYAN}$*${NC}"; }
ok() { echo -e "${GREEN}$*${NC}"; }
warn() { echo -e "${YELLOW}$*${NC}"; }
err() { echo -e "${RED}$*${NC}"; }

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        err "این اسکریپت باید با root اجرا شود."
        echo "مثال: sudo bash install.sh"
        exit 1
    fi
}

pause() {
    echo
    read -r -p "برای بازگشت Enter بزنید..."
}

install_deps() {
    msg "در حال نصب وابستگی‌ها..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y python3 python3-venv python3-pip mariadb-server mariadb-client git rsync openssl cron curl
    systemctl enable --now mariadb
    systemctl enable --now cron
    ok "وابستگی‌ها نصب شدند."
}

make_env() {
    if [[ -f "$ENV_FILE" ]]; then
        warn "فایل .env از قبل وجود دارد؛ بدون تغییر نگه داشته شد."
        return
    fi

    echo
    read -r -p "BOT_TOKEN: " BOT_TOKEN
    read -r -p "ADMIN_TELEGRAM_IDS (مثلاً 123456789): " ADMIN_IDS
    read -r -p "DB_PASSWORD برای hashbot: " DB_PASSWORD
    DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -hex 16)}"
    SECRET_KEY="$(openssl rand -hex 32)"

    cat > "$ENV_FILE" <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_TELEGRAM_IDS=${ADMIN_IDS}
TIMEZONE=Asia/Tehran
SECRET_KEY=${SECRET_KEY}

DB_HOST=127.0.0.1
DB_USER=hashbot
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=hashbot

BACKUP_DIR=${BACKUP_DIR}
NOBITEX_API_KEY=
EOF
    chmod 600 "$ENV_FILE"
    ok ".env ساخته شد."
}

setup_db() {
    msg "در حال آماده‌سازی MariaDB..."
    systemctl enable --now mariadb

    if [[ ! -f "$ENV_FILE" ]]; then
        warn "ابتدا گزینه 4 را اجرا کنید تا .env ساخته شود."
        return 1
    fi

    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a

    mysql -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

    if [[ -f "${APP_DIR}/schema.sql" ]]; then
        mysql -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}" < "${APP_DIR}/schema.sql"
    else
        warn "schema.sql پیدا نشد؛ ساخت جدول‌ها انجام نشد."
    fi

    ok "دیتابیس آماده شد."
}

install_python() {
    if [[ ! -d "${APP_DIR}/.venv" ]]; then
        python3 -m venv "${APP_DIR}/.venv"
    fi

    "${APP_DIR}/.venv/bin/pip" install --upgrade pip
    if [[ -f "${APP_DIR}/requirements.txt" ]]; then
        "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
    fi

    ok "محیط Python آماده شد."
}

install_service() {
    if [[ ! -f "${APP_DIR}/app/bot.py" ]]; then
        err "app/bot.py پیدا نشد."
        return 1
    fi

    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=HashBot Telegram Bot
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/app/bot.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

    # وب‌سرویس قدیمی دیگر استفاده نمی‌شود.
    systemctl disable --now hashbot-web.service 2>/dev/null || true
    rm -f /etc/systemd/system/hashbot-web.service

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"

    ok "سرویس Bot نصب و فعال شد."
}

setup_backup() {
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"

    cat > /usr/local/bin/hashbot-backup <<EOF
#!/usr/bin/env bash
set -e
set -a
source "${ENV_FILE}"
set +a
mkdir -p "\${BACKUP_DIR}"
STAMP="\$(date +%Y%m%d_%H%M%S)"
mysqldump -h"\${DB_HOST}" -u"\${DB_USER}" -p"\${DB_PASSWORD}" "\${DB_NAME}" | gzip > "\${BACKUP_DIR}/db_\${STAMP}.sql.gz"
find "\${BACKUP_DIR}" -type f -name 'db_*.sql.gz' -mtime +7 -delete
EOF
    chmod 700 /usr/local/bin/hashbot-backup

    cat > /etc/cron.d/hashbot-backup <<EOF
0 4 * * * root /usr/local/bin/hashbot-backup >/dev/null 2>&1
EOF
    chmod 644 /etc/cron.d/hashbot-backup
    systemctl restart cron

    ok "بکاپ روزانه تنظیم شد."
}

status_bot() {
    echo
    systemctl --no-pager --full status "$SERVICE_NAME" || true
    echo
    echo "آخرین لاگ‌ها:"
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager || true
}

restart_bot() {
    systemctl restart "$SERVICE_NAME"
    ok "Bot ری‌استارت شد."
    systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,12p' || true
}

logs_bot() {
    journalctl -u "$SERVICE_NAME" -n 100 --no-pager
}

full_install() {
    install_deps
    make_env
    setup_db
    install_python
    install_service
    setup_backup
    ok "نصب کامل HashBot انجام شد."
}

menu() {
    clear
    echo "╔══════════════════════════════════════╗"
    echo "║          HashBot Installer           ║"
    echo "╚══════════════════════════════════════╝"
    echo
    echo "1) نصب کامل HashBot"
    echo "2) نصب وابستگی‌ها"
    echo "3) ساخت دیتابیس"
    echo "4) تنظیم فایل .env"
    echo "5) نصب و فعال‌سازی سرویس Bot"
    echo "6) تنظیم بکاپ خودکار"
    echo "7) بررسی وضعیت نصب"
    echo "8) ری‌استارت Bot"
    echo "9) نمایش لاگ Bot"
    echo "0) خروج"
    echo
    read -r -p "انتخاب شما: " choice
    echo

    case "$choice" in
        1) full_install ;;
        2) install_deps ;;
        3) setup_db ;;
        4) make_env ;;
        5) install_python && install_service ;;
        6) setup_backup ;;
        7) status_bot ;;
        8) restart_bot ;;
        9) logs_bot ;;
        0) exit 0 ;;
        *) warn "گزینه نامعتبر است." ;;
    esac

    pause
}

require_root

while true; do
    menu
done
