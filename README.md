<img width="1920" height="919" alt="tegrako-bot" src="https://github.com/user-attachments/assets/443415fc-82b1-4011-9634-72071de3f9d1" />

# Tegrako Bot

Telegram-бот для TegrakoVPN на базе Remnawave. Управляет подписками, платежами, поддержкой и MTProto-прокси прямо из Telegram.

---

## Возможности

**Пользователь**
- Регистрация, личный кабинет, история платежей
- Покупка и продление подписки, выбор тарифа
- Управление устройствами (HWID)
- MTProto-прокси с автопровизионингом
- Реферальная программа с бонусными днями
- Поддержка через тикеты

**Администратор**
- Управление тарифами (цена, трафик, устройства, сквад, триал)
- Подтверждение/отклонение платежей по скриншоту
- Ответы на тикеты, ручное закрытие
- Управление нодами Remnawave
- Рассылка по сегментам (все / активные / истёкшие)
- Статистика выручки и пользователей
- Режим технических работ

**Сервисное**
- Вебхуки от Remnawave → мгновенные уведомления об истечении и лимитах
- Bulk-запрос к панели в scheduler (один запрос вместо N по uuid)
- Scheduler раз в 6 часов как fallback
- Автоотзыв MTProto при просрочке > 5 дней

---

## Стек

| | |
|---|---|
| Python | 3.12 |
| Telegram | aiogram 3.26 |
| Web | aiohttp (webhook-сервер) |
| БД | PostgreSQL + SQLAlchemy async |
| Панель | Remnawave (httpx, прямые запросы) |
| Деплой | Docker Compose |

---

## Структура

```
.
├── bot/
│   ├── handlers/
│   │   ├── user/        # start, payment, support, mtproto
│   │   ├── admin/       # admin
│   │   └── webhook.py   # события от Remnawave
│   ├── middlewares/
│   ├── services/
│   │   ├── remnawave.py
│   │   ├── scheduler.py
│   │   └── telemt.py
│   ├── keyboards/
│   └── states/
├── config/settings.py
├── db/
│   ├── models.py
│   └── dal.py
├── main.py
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── requirements.txt
```

---

## Установка

### 1. Docker

```bash
sudo apt update && sudo apt install docker.io docker-compose-plugin -y
sudo systemctl enable --now docker
```

Если Remnawave уже стоит — Docker есть, шаг пропускай.

### 2. Клонирование

```bash
git clone https://github.com/slamare/tegrako-bot
cd tegrako-bot
```

### 3. `.env`

```bash
cp .env.example .env
nano .env
```

Обязательные переменные:

```env
BOT_TOKEN=
ADMIN_IDS=

DATABASE_URL=postgresql+asyncpg://tegrakobot:password@db:5432/tegrakobot
POSTGRES_PASSWORD=

PANEL_API_URL=https://your-panel-domain.com
PANEL_API_KEY=
DEFAULT_SQUAD_UUID=

BOT_NAME=TegrakoVPN

# Реквизиты оплаты: "Название|Реквизиты" через ;
PAYMENT_REQUISITES=

NOTIFY_EXPIRY_DAYS=3,1

# Вебхуки от Remnawave (секрет должен совпадать с WEBHOOK_SECRET_HEADER в панели)
WEBHOOK_SECRET=
WEBHOOK_PORT=9090
```

Опциональные:

```env
WELCOME_IMAGE_URL=
DEVICE_SLOT_PRICE=0

# MTProto прокси (telemt)
TELEMT_CONFIG_PATH=/opt/telemt/config/telemt.toml
TELEMT_API_URL=http://host.docker.internal:9091
TELEMT_PUBLIC_HOST=
TELEMT_PUBLIC_PORT=8443
```

### 4. Docker network

Бот должен быть в одной сети с Remnawave:

```bash
docker network ls | grep remnawave-network
```

Если нет:

```bash
docker network create remnawave-network
```

### 5. Запуск

```bash
docker compose up -d --build
```

---

## Вебхуки Remnawave

Бот слушает события панели на порту `9090`. Remnawave шлёт `POST /webhook` с заголовком `X-Webhook-Secret`.

В `/opt/remnawave/.env`:

```env
WEBHOOK_ENABLED=true
WEBHOOK_URL=http://tegrakobot:9090/webhook
WEBHOOK_SECRET_HEADER=<тот же секрет что в WEBHOOK_SECRET бота>
```

Обрабатываемые события: `user.expired`, `user.limited`, `user.disabled`, `user.expires_in_24/48/72_hours`.

---

## Обновление

```bash
cd /opt/tegrakobot
git stash && git pull && git stash drop
docker compose build --no-cache tegrakobot && docker compose up -d tegrakobot
```

---

## Логи

```bash
docker logs tegrakobot -f
```

---

## Диагностика

**Бот не отвечает** — проверь `BOT_TOKEN`, контейнер запущен (`docker ps`), сеть доступна.

**502 от панели** — панель ещё поднимается, подожди 30 сек, проверь `docker logs remnawave`.

**Вебхук возвращает 403** — секрет в боте и панели не совпадают.

**Ошибка БД** — проверь `DATABASE_URL` и что контейнер `tegrakobot-db` жив.

---

## License

[GLWTPL](https://github.com/me-shaon/GLWTPL/)
