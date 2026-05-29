<img width="1920" height="919" alt="screencapture-file-E-tegrako-monument-2-html-2026-05-18-18_03_00" src="https://github.com/user-attachments/assets/443415fc-82b1-4011-9634-72071de3f9d1" />

# Tegrako Bot

Telegram-бот для работы с Remnawave.

Позволяет управлять пользователями, подписками и сервисными задачами прямо через Telegram без необходимости постоянно заходить в панель.

---

## 🚀 Возможности

### Пользователь

- `/start`
- управление подпиской
- взаимодействие с сервисом через Telegram
- обращения в поддержку
- работа с личными данными

### Администратор

- административные команды
- управление пользователями
- модерация
- проверка ограничений

### Сервисное

- интеграция с Remnawave API
- PostgreSQL
- фоновые задачи через scheduler
- middleware
- Docker deployment

---

## 🛠 Stack

| Что | Используется |
|---|---|
| Язык | Python 3.12 |
| Telegram | aiogram 3 |
| База | PostgreSQL |
| ORM | SQLAlchemy |
| API | Remnawave |
| Контейнеры | Docker / Docker Compose |

---

## 📁 Структура проекта

```bash
.
├── bot/
│   ├── handlers/
│   │   ├── user/
│   │   └── admin/
│   ├── middlewares/
│   ├── services/
│   ├── states/
│   └── utils/
│
├── config/
├── db/
├── main.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

# ⚙️ Установка

## 1. Установка Docker

Перед запуском нужен Docker и Docker Compose.

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install docker.io docker-compose-plugin -y
sudo systemctl enable docker
sudo systemctl start docker
```

### Проверка

```bash
docker --version
docker compose version
```

> [!NOTE]
> Если Remnawave Panel устанавливалась по официальной инструкции:
>
> https://docs.rw/docs/install/remnawave-panel
>
> Docker уже должен быть установлен, этот шаг можно пропустить.

---

## 2. Клонирование репозитория

```bash
git clone https://github.com/slamare/tegrako-bot
cd tegrako-bot
```

---

## 3. Настройка `.env`

Создай файл:

```bash
cp .env.example .env
```

Заполни значения:

```env
BOT_TOKEN=

ADMIN_IDS=

DATABASE_URL=

REMNAWAVE_URL=
REMNAWAVE_TOKEN=
```

---

## 4. Проверка Docker network

В `docker-compose.yml` используется внешняя сеть:

```yaml
remnawave-network:
  external: true
```

Проверка:

```bash
docker network ls
```

Если сети нет:

```bash
docker network create remnawave-network
```

---

## 5. Запуск

```bash
docker compose up -d --build
```

Проверка:

```bash
docker ps
```

---

# 📜 Логи

### Все сервисы

```bash
docker compose logs -f
```

### Только бот

```bash
docker logs tegrabot -f
```

---

# 🗄 PostgreSQL

Данные сохраняются в volume:

```bash
tegrabot-db-data
```

Проверка:

```bash
docker volume ls
```

---

# 🔗 Работа с Remnawave

Бот подключается к панели через API.

Проверь:

- корректный `REMNAWAVE_URL`
- актуальный `REMNAWAVE_TOKEN`
- контейнер панели подключён к `remnawave-network`

---

# 🔄 Обновление

```bash
git pull
docker compose up -d --build
```

---

# 🧩 Если что-то не работает

## Бот не отвечает

Проверь:

- `BOT_TOKEN`
- контейнер поднят
- доступ к интернету

---

## Ошибка базы данных

Проверь:

- `DATABASE_URL`
- PostgreSQL контейнер работает

---

## Нет подключения к Remnawave

Проверь:

- URL панели
- API токен
- docker network

---

## Контейнер завершился

```bash
docker compose logs -f
```

---
