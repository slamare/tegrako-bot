from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Telegram ───────────────────────────────────────────────────────────
    BOT_TOKEN: str
    ADMIN_IDS: str  # "123456,789012" через запятую

    # ── Database ───────────────────────────────────────────────────────────
    DATABASE_URL: str  # postgresql+asyncpg://user:pass@host/dbname

    # ── Remnawave panel ────────────────────────────────────────────────────
    PANEL_API_URL: str        # https://panel.example.com/api
    PANEL_API_KEY: str

    # ── Bot UI ─────────────────────────────────────────────────────────────
    BOT_NAME: str = "TegraVPN"
    WELCOME_IMAGE_URL: Optional[str] = None   # URL картинки для /start
    SUPPORT_LINK: Optional[str] = None        # Ссылка в описании поддержки

    # ── Payment requisites (можно несколько через ; ) ──────────────────────
    # Формат: "label|details" через ;
    # Пример: "Карта Сбер|2200 1234 5678 9012 Иван И.;СБП|+7 999 000 11 22"
    PAYMENT_REQUISITES: str = ""

    # ── Notifications ──────────────────────────────────────────────────────
    # За сколько дней до истечения слать напоминание (через запятую)
    NOTIFY_EXPIRY_DAYS: str = "3,1"

    @property
    def admin_ids(self) -> list[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip()]

    @property
    def payment_requisites(self) -> list[dict]:
        """Возвращает список {'label': ..., 'details': ...}"""
        items = []
        for item in self.PAYMENT_REQUISITES.split(";"):
            item = item.strip()
            if "|" in item:
                label, details = item.split("|", 1)
                items.append({"label": label.strip(), "details": details.strip()})
        return items

    @property
    def notify_expiry_days(self) -> list[int]:
        return [int(x.strip()) for x in self.NOTIFY_EXPIRY_DAYS.split(",") if x.strip()]


settings = Settings()
