from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    BOT_TOKEN: str
    ADMIN_IDS: str

    # Database
    DATABASE_URL: str

    # Remnawave panel
    PANEL_API_URL: str
    PANEL_API_KEY: str
    DEFAULT_SQUAD_UUID: Optional[str] = None

    # Bot UI
    BOT_NAME: str = "TegraVPN"
    WELCOME_IMAGE_URL: Optional[str] = None
    SUPPORT_LINK: Optional[str] = None

    # Payment
    PAYMENT_REQUISITES: str = ""
    DEVICE_SLOT_PRICE: float = 0.0

    # Notifications
    NOTIFY_EXPIRY_DAYS: str = "3,1"

    # Webhook от Remnawave → бот
    # Секрет задаётся в панели и здесь: /api/webhook?secret=<WEBHOOK_SECRET>
    WEBHOOK_SECRET: Optional[str] = None
    # Порт, на котором бот слушает вебхуки панели (не Telegram-вебхук)
    WEBHOOK_PORT: int = 9090

    @property
    def admin_ids(self) -> list[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip()]

    @property
    def payment_requisites(self) -> list[dict]:
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
