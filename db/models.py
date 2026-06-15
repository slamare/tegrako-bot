from datetime import datetime
from typing import Optional
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Integer, Numeric, String, Text, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    remnawave_username: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    remnawave_uuid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    is_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(16), default="user")  # user / admin / developer
    extra_device_slots: Mapped[int] = mapped_column(Integer, default=0)
    mtproto_secret: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    referred_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    payments: Mapped[list["Payment"]] = relationship(back_populates="user", lazy="selectin")
    tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="user", lazy="selectin")


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=0)
    device_limit: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    is_referral: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    squad_uuid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    payments: Mapped[list["Payment"]] = relationship(back_populates="tariff", lazy="selectin")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    tariff_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tariffs.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    payment_type: Mapped[str] = mapped_column(String(16), default="subscription")  # subscription / device_slot / promo_test
    status: Mapped[str] = mapped_column(String(16), default="pending")
    payment_method: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    screenshot_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    admin_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    approved_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    promo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("promo_codes.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="payments")
    tariff: Mapped[Optional["Tariff"]] = relationship(back_populates="payments")
    promo: Mapped[Optional["PromoCode"]] = relationship(back_populates="payments")


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)   # 0–100
    discount_fixed: Mapped[float] = mapped_column(Numeric(10, 2), default=0)  # фиксированная скидка в ₽
    tariff_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tariffs.id"), nullable=True)  # None = для любого тарифа
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    payments: Mapped[list["Payment"]] = relationship(back_populates="promo")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="tickets")
    messages: Mapped[list["TicketMessage"]] = relationship(back_populates="ticket", lazy="selectin")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), nullable=False, index=True)
    sender_role: Mapped[str] = mapped_column(String(8), nullable=False)
    sender_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    tg_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    ticket: Mapped["SupportTicket"] = relationship(back_populates="messages")


class BotSettings(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    meta: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CustomMenuButton(Base):
    """Дополнительные кнопки главного меню, настраиваемые из админки."""
    __tablename__ = "custom_menu_buttons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    condition: Mapped[str] = mapped_column(String(16), default="all")  # all / active_sub
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
