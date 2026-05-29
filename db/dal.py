from typing import Optional
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import User, Tariff, Payment, SupportTicket, TicketMessage, BotSettings, Notification


# ── Users ──────────────────────────────────────────────────────────────────

async def get_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_user_by_remnawave_username(session: AsyncSession, username: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.remnawave_username == username))
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, telegram_id: int, username: Optional[str] = None) -> User:
    user = User(telegram_id=telegram_id, username=username)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def update_user(session: AsyncSession, telegram_id: int, **kwargs) -> None:
    await session.execute(update(User).where(User.telegram_id == telegram_id).values(**kwargs))
    await session.commit()


async def get_all_users(session: AsyncSession, only_registered: bool = False) -> list[User]:
    q = select(User)
    if only_registered:
        q = q.where(User.is_registered == True)
    result = await session.execute(q)
    return result.scalars().all()


async def count_users(session: AsyncSession) -> dict:
    total = await session.scalar(select(func.count(User.id)))
    registered = await session.scalar(select(func.count(User.id)).where(User.is_registered == True))
    banned = await session.scalar(select(func.count(User.id)).where(User.is_banned == True))
    return {"total": total, "registered": registered, "banned": banned}


# ── Tariffs ────────────────────────────────────────────────────────────────

async def get_active_tariffs(session: AsyncSession) -> list[Tariff]:
    result = await session.execute(
        select(Tariff).where(Tariff.is_active == True).order_by(Tariff.sort_order, Tariff.price)
    )
    return result.scalars().all()


async def get_tariff(session: AsyncSession, tariff_id: int) -> Optional[Tariff]:
    return await session.get(Tariff, tariff_id)


async def create_tariff(session: AsyncSession, **kwargs) -> Tariff:
    tariff = Tariff(**kwargs)
    session.add(tariff)
    await session.commit()
    await session.refresh(tariff)
    return tariff


async def update_tariff(session: AsyncSession, tariff_id: int, **kwargs) -> None:
    await session.execute(update(Tariff).where(Tariff.id == tariff_id).values(**kwargs))
    await session.commit()


async def delete_tariff(session: AsyncSession, tariff_id: int) -> None:
    tariff = await session.get(Tariff, tariff_id)
    if tariff:
        await session.delete(tariff)
        await session.commit()


async def get_all_tariffs(session: AsyncSession) -> list[Tariff]:
    result = await session.execute(select(Tariff).order_by(Tariff.sort_order, Tariff.price))
    return result.scalars().all()


# ── Payments ───────────────────────────────────────────────────────────────

async def create_payment(session: AsyncSession, user_id: int, tariff_id: int,
                          amount: float, payment_method: str,
                          screenshot_file_id: Optional[str] = None) -> Payment:
    payment = Payment(
        user_id=user_id,
        tariff_id=tariff_id,
        amount=amount,
        payment_method=payment_method,
        screenshot_file_id=screenshot_file_id,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def get_payment(session: AsyncSession, payment_id: int) -> Optional[Payment]:
    return await session.get(Payment, payment_id)


async def update_payment(session: AsyncSession, payment_id: int, **kwargs) -> None:
    await session.execute(update(Payment).where(Payment.id == payment_id).values(**kwargs))
    await session.commit()


async def get_user_payments(session: AsyncSession, user_id: int) -> list[Payment]:
    result = await session.execute(
        select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc())
    )
    return result.scalars().all()


async def get_pending_payments(session: AsyncSession) -> list[Payment]:
    result = await session.execute(
        select(Payment).where(Payment.status == "pending").order_by(Payment.created_at)
    )
    return result.scalars().all()


async def get_revenue_stats(session: AsyncSession) -> dict:
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=now.weekday())

    total = await session.scalar(
        select(func.sum(Payment.amount)).where(Payment.status == "approved")
    ) or 0
    monthly = await session.scalar(
        select(func.sum(Payment.amount)).where(
            Payment.status == "approved", Payment.updated_at >= month_start
        )
    ) or 0
    weekly = await session.scalar(
        select(func.sum(Payment.amount)).where(
            Payment.status == "approved", Payment.updated_at >= week_start
        )
    ) or 0

    return {"total": float(total), "monthly": float(monthly), "weekly": float(weekly)}


# ── Support tickets ────────────────────────────────────────────────────────

async def get_open_ticket(session: AsyncSession, user_id: int) -> Optional[SupportTicket]:
    result = await session.execute(
        select(SupportTicket).where(
            SupportTicket.user_id == user_id,
            SupportTicket.status == "open"
        )
    )
    return result.scalar_one_or_none()


async def create_ticket(session: AsyncSession, user_id: int) -> SupportTicket:
    ticket = SupportTicket(user_id=user_id)
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


async def close_ticket(session: AsyncSession, ticket_id: int) -> None:
    await session.execute(update(SupportTicket).where(SupportTicket.id == ticket_id).values(status="closed"))
    await session.commit()


async def add_ticket_message(session: AsyncSession, ticket_id: int, sender_role: str,
                              sender_tg_id: int, text: Optional[str] = None,
                              media_file_id: Optional[str] = None,
                              media_type: Optional[str] = None,
                              tg_message_id: Optional[int] = None) -> TicketMessage:
    msg = TicketMessage(
        ticket_id=ticket_id,
        sender_role=sender_role,
        sender_tg_id=sender_tg_id,
        text=text,
        media_file_id=media_file_id,
        media_type=media_type,
        tg_message_id=tg_message_id,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def get_open_tickets(session: AsyncSession) -> list[SupportTicket]:
    result = await session.execute(
        select(SupportTicket).where(SupportTicket.status == "open").order_by(SupportTicket.updated_at)
    )
    return result.scalars().all()


async def get_ticket_by_id(session: AsyncSession, ticket_id: int) -> Optional[SupportTicket]:
    return await session.get(SupportTicket, ticket_id)


# ── Bot settings ───────────────────────────────────────────────────────────

async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    result = await session.get(BotSettings, key)
    return result.value if result else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    existing = await session.get(BotSettings, key)
    if existing:
        existing.value = value
    else:
        session.add(BotSettings(key=key, value=value))
    await session.commit()


# ── Notifications ──────────────────────────────────────────────────────────

async def was_notified(session: AsyncSession, user_id: int, notif_type: str, meta: Optional[str] = None) -> bool:
    from datetime import datetime, timedelta
    since = datetime.utcnow() - timedelta(days=1)
    q = select(Notification).where(
        Notification.user_id == user_id,
        Notification.type == notif_type,
        Notification.sent_at >= since,
    )
    if meta:
        q = q.where(Notification.meta == meta)
    result = await session.execute(q)
    return result.scalar_one_or_none() is not None


async def log_notification(session: AsyncSession, user_id: int, notif_type: str,
                            meta: Optional[str] = None) -> None:
    session.add(Notification(user_id=user_id, type=notif_type, meta=meta))
    await session.commit()
