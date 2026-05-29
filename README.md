# TegrakoBot

Telegram-бот для продажи VPN-подписок на базе [Remnawave](https://remna.st).

## Возможности

- 👤 Регистрация через `@username` Telegram или ввод вручную с проверкой уникальности в панели
- 🔄 Автоопределение существующих пользователей Remnawave при миграции
- 🛒 Покупка подписки: выбор тарифа → реквизиты → скриншот оплаты
- ✅ Ручное подтверждение платежей администратором
- 👤 Личный кабинет: статус подписки, ссылка, устройства, история платежей
- 🎫 Встроенная поддержка: тикеты прямо в боте, ответы через бота
- 📦 Конструктор тарифов в боте (лимит трафика, устройств, срок, цена)
- 📡 Управление нодами: просмотр статуса, перезагрузка
- 🔧 Режим тех. работ с автоуведомлением пользователей
- ⏰ Автоматические уведомления об истечении подписки
- 📢 Рассылка по сегментам (все / активные / истёкшие)
- 🚫 Бан пользователей
- 📊 Статистика: выручка, пользователи, ноды

## Стек

- Python 3.12
- aiogram 3.x
- SQLAlchemy 2.x + asyncpg (PostgreSQL)
- [remnawave Python SDK](https://github.com/remnawave/python-sdk)
- Docker + Docker Compose

## Установка

### 1. Клонируй репозиторий

```bash
git clone https://github.com/ТВО_ЛОГИН/tegrabot.git
cd tegrabot
```

### 2. Настрой переменные окружения

```bash
cp .env.example .env
nano .env
```

Заполни обязательные поля:

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | Твой Telegram ID (можно несколько через запятую) |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@db/tegrabot` |
| `PANEL_API_URL` | URL твоей панели Remnawave |
| `PANEL_API_KEY` | API ключ из панели (раздел API Tokens) |
| `PAYMENT_REQUISITES` | Реквизиты в формате `Название\|Реквизиты;Название2\|Реквизиты2` |

### 3. Запусти

```bash
docker compose up -d
```

### 4. Создай первый тариф

Напиши боту `/admin` → **Тарифы** → **➕ Создать тариф**

## Структура проекта

```
tegrabot/
├── bot/
│   ├── handlers/
│   │   ├── user/          # start, payment, support
│   │   └── admin/         # admin panel
│   ├── keyboards/         # inline & reply keyboards
│   ├── middlewares/       # db session, ban check
│   ├── services/
│   │   ├── remnawave.py   # Remnawave SDK wrapper
│   │   └── scheduler.py   # уведомления об истечении
│   └── states/            # FSM states
├── config/
│   └── settings.py        # pydantic-settings конфиг
├── db/
│   ├── models.py          # SQLAlchemy модели
│   ├── database.py        # подключение к БД
│   └── dal.py             # Data Access Layer
├── main.py
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Команды администратора

| Команда | Описание |
|---|---|
| `/admin` | Открыть панель администратора |

Через панель доступно: статистика, пользователи, платежи, тикеты, тарифы, ноды, рассылка, тех. работы.

## Миграция с Remnashop

Бот автоматически определяет существующих пользователей Remnawave по `telegram_id` при первом `/start`. Пользователям не нужно ничего делать — их подписки сохранятся.

## Лицензия

MIT
