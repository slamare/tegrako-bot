#!/usr/bin/env bash
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

info()  { echo -e "${GREEN}==>${RESET} $1"; }
warn()  { echo -e "${YELLOW}!!${RESET} $1"; }
fail()  { echo -e "${RED}xx${RESET} $1"; exit 1; }

ENV_FILE=".env"

# ── Проверки окружения ───────────────────────────────────────────────────

command -v docker >/dev/null 2>&1 || fail "Docker не найден. Установи Docker перед запуском: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin не найден (нужна команда 'docker compose')."

random_hex() {
    local bytes="$1"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    else
        python3 -c "import secrets; print(secrets.token_hex($bytes))"
    fi
}

# ── Ввод значений ─────────────────────────────────────────────────────────

ask_required() {
    local prompt="$1"
    local value=""
    while [ -z "$value" ]; do
        read -rp "$(echo -e "${BOLD}${prompt}${RESET}: ")" value
        [ -z "$value" ] && warn "Значение обязательно, попробуй ещё раз."
    done
    echo "$value"
}

ask_default() {
    local prompt="$1"
    local default="$2"
    local value=""
    read -rp "$(echo -e "${BOLD}${prompt}${RESET} [${default}]: ")" value
    echo "${value:-$default}"
}

ask_yn() {
    local prompt="$1"
    local default="${2:-n}"
    local hint="y/N"
    [ "$default" = "y" ] && hint="Y/n"
    local value=""
    read -rp "$(echo -e "${BOLD}${prompt}${RESET} [${hint}]: ")" value
    value="${value:-$default}"
    [[ "$value" =~ ^[Yy] ]]
}

echo -e "${BOLD}Tegrako Bot — установка${RESET}\n"

if [ -f "$ENV_FILE" ]; then
    warn "Файл .env уже существует."
    if ! ask_yn "Перезаписать его?" n; then
        info "Оставляю текущий .env без изменений. Дальше — только docker compose up."
        SKIP_ENV=1
    fi
fi

if [ -z "${SKIP_ENV:-}" ]; then
    echo -e "\n${BOLD}1/5 — Telegram${RESET}"
    BOT_TOKEN=$(ask_required "Токен бота от @BotFather")
    ADMIN_IDS=$(ask_required "Твой Telegram ID (число, через запятую если несколько админов)")

    echo -e "\n${BOLD}2/5 — Remnawave Panel${RESET}"
    PANEL_API_URL=$(ask_required "URL панели (например https://panel.example.com)")
    PANEL_API_KEY=$(ask_required "API-ключ панели")
    DEFAULT_SQUAD_UUID=$(ask_default "UUID дефолтного сквада (Internal Squads в панели, можно пусто)" "")
    ADMIN_GRANT_SQUAD_UUID=$(ask_default "UUID сквада для тарифов, выданных админом без оплаты (можно пусто)" "")

    echo -e "\n${BOLD}3/5 — База данных${RESET}"
    if ask_yn "Сгенерировать пароль Postgres автоматически?" y; then
        POSTGRES_PASSWORD=$(random_hex 24)
        info "Пароль сгенерирован."
    else
        POSTGRES_PASSWORD=$(ask_required "Пароль для Postgres")
    fi
    DATABASE_URL="postgresql+asyncpg://tegrakobot:${POSTGRES_PASSWORD}@db:5432/tegrakobot"

    echo -e "\n${BOLD}4/5 — Бот и оплата${RESET}"
    BOT_NAME=$(ask_default "Название бота (видно пользователям)" "TegrakoVPN")
    DEVICE_SLOT_PRICE=$(ask_default "Цена доп. слота устройства, ₽ (0 = отключено)" "0")
    REFERRAL_DISCOUNT_PERCENT=$(ask_default "Скидка для приглашённых на первую покупку, %" "5")
    PAYMENT_REQUISITES=$(ask_default "Реквизиты оплаты (\"Название|Реквизиты\" через ;, можно заполнить позже в .env)" "")

    echo -e "\n${BOLD}5/5 — Webhook и прокси${RESET}"
    WEBHOOK_SECRET=$(random_hex 32)
    info "Секрет вебхука сгенерирован — вставь его в настройки Webhook в панели Remnawave."
    if ask_yn "Настроить MTProto-прокси (telemt) сейчас?" n; then
        TELEMT_API_URL=$(ask_required "URL telemt API (например http://host.docker.internal:9091)")
        TELEMT_PUBLIC_HOST=$(ask_required "Публичный хост telemt (для ссылок пользователям)")
        TELEMT_PUBLIC_PORT=$(ask_default "Публичный порт telemt" "8443")
    else
        TELEMT_API_URL=""
        TELEMT_PUBLIC_HOST=""
        TELEMT_PUBLIC_PORT="8443"
    fi
    TELEGRAM_BOT_PROXY=$(ask_default "SOCKS5/HTTP прокси для Telegram API (пусто, если не нужен)" "")

    cat > "$ENV_FILE" <<EOF
# Сгенерировано install.sh $(date -Iseconds)

# Telegram
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
TELEGRAM_BOT_PROXY=${TELEGRAM_BOT_PROXY}

# Database
DATABASE_URL=${DATABASE_URL}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# Remnawave Panel
PANEL_API_URL=${PANEL_API_URL}
PANEL_API_KEY=${PANEL_API_KEY}
DEFAULT_SQUAD_UUID=${DEFAULT_SQUAD_UUID}
ADMIN_GRANT_SQUAD_UUID=${ADMIN_GRANT_SQUAD_UUID}

# Bot UI
BOT_NAME=${BOT_NAME}
WELCOME_IMAGE_URL=

# Payment
PAYMENT_REQUISITES=${PAYMENT_REQUISITES}
DEVICE_SLOT_PRICE=${DEVICE_SLOT_PRICE}
REFERRAL_DISCOUNT_PERCENT=${REFERRAL_DISCOUNT_PERCENT}

# Notifications
NOTIFY_EXPIRY_DAYS=3,1

# Webhook (Remnawave -> бот)
WEBHOOK_SECRET=${WEBHOOK_SECRET}
WEBHOOK_PORT=9090

# MTProto прокси (telemt), опционально
TELEMT_API_URL=${TELEMT_API_URL}
TELEMT_PUBLIC_HOST=${TELEMT_PUBLIC_HOST}
TELEMT_PUBLIC_PORT=${TELEMT_PUBLIC_PORT}

# Резервные копии БД
BACKUP_INTERVAL_SECONDS=21600
BACKUP_RETENTION_DAYS=14
EOF

    info ".env записан."
fi

# ── Docker network ───────────────────────────────────────────────────────

if ! docker network inspect remnawave-network >/dev/null 2>&1; then
    warn "Внешняя сеть 'remnawave-network' не найдена."
    if ask_yn "Создать её сейчас?" y; then
        docker network create remnawave-network
        info "Сеть создана."
    else
        fail "Без сети 'remnawave-network' бот не сможет достучаться до панели. Создай её вручную: docker network create remnawave-network"
    fi
fi

# ── Запуск ───────────────────────────────────────────────────────────────

if ask_yn "Собрать и запустить контейнеры сейчас?" y; then
    docker compose up -d --build
    echo
    info "Запущено. Смотри логи: docker compose logs -f tegrakobot"
else
    info "Готово. Запусти вручную: docker compose up -d --build"
fi
