from typing import Optional
from datetime import datetime, timedelta
from sqlalchemy import select, update, func, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from db.models import (
    User, Tariff, Payment, SupportTicket, TicketMessage,
    BotSettings, Notification, PromoCode, CustomMenuButton,
)


# ── Users ──────────────────────────────────────────────────────────────────

async def get_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_user_by_remnawave_username(session: AsyncSession, username: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.remnawave_username == username))
    return result.scalar_one_or_none()


async def get_user_by_uuid(session: AsyncSession, uuid: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.remnawave_uuid == uuid))
    return result.scalar_one_or_none()


async def search_users(session: AsyncSession, query: str) -> list[User]:
    q = query.strip().lstrip("@")
    filters = [User.username.ilike(f"%{q}%"), User.remnawave_username.ilike(f"%{q}%")]
    try:
        filters.append(User.telegram_id == int(q))
    except ValueError:
        pass
    result = await session.execute(select(User).where(or_(*filters)).limit(20))
    return result.scalars().all()


async def create_user(
    session: AsyncSession, telegram_id: int,
    username: Optional[str] = None, referred_by: Optional[int] = None,
) -> User:
    user = User(telegram_id=telegram_id, username=username, referred_by=referred_by)
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


async def get_banned_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).where(User.is_banned == True))
    return result.scalars().all()


async def count_users(session: AsyncSession) -> dict:
    total = await session.scalar(select(func.count(User.id)))
    registered = await session.scalar(select(func.count(User.id)).where(User.is_registered == True))
    banned = await session.scalar(select(func.count(User.id)).where(User.is_banned == True))
    return {"total": total, "registered": registered, "banned": banned}


async def count_referrals(session: AsyncSession, telegram_id: int) -> int:
    return await session.scalar(
        select(func.count(User.id)).where(User.referred_by == telegram_id)
    ) or 0


async def get_referrals_with_payment(session: AsyncSession, telegram_id: int) -> list[User]:
    result = await session.execute(
        select(User)
        .where(User.referred_by == telegram_id)
        .join(Payment, Payment.user_id == User.id)
        .where(Payment.status == "approved")
        .distinct()
    )
    return result.scalars().all()


# ── Tariffs ────────────────────────────────────────────────────────────────

async def get_active_tariffs(session: AsyncSession) -> list[Tariff]:
    result = await session.execute(
        select(Tariff).where(Tariff.is_active == True).order_by(Tariff.sort_order, Tariff.price)
    )
    return result.scalars().all()


async def get_tariff(session: AsyncSession, tariff_id: int) -> Optional[Tariff]:
    return await session.get(Tariff, tariff_id)


async def get_all_tariffs(session: AsyncSession) -> list[Tariff]:
    result = await session.execute(select(Tariff).order_by(Tariff.sort_order, Tariff.price))
    return result.scalars().all()


async def create_tariff(session: AsyncSession, **kwargs) -> Tariff:
    allowed = {
        "name", "description", "duration_days", "traffic_limit_gb",
        "device_limit", "price", "is_active", "is_trial", "is_referral",
        "sort_order", "squad_uuid",
    }
    tariff = Tariff(**{k: v for k, v in kwargs.items() if k in allowed})
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


# ── Payments ───────────────────────────────────────────────────────────────

async def create_payment(
    session: AsyncSession,
    user_id: int,
    tariff_id: Optional[int],
    amount: float,
    payment_method: str,
    screenshot_file_id: Optional[str] = None,
    payment_type: str = "subscription",
    promo_id: Optional[int] = None,
) -> Payment:
    payment = Payment(
        user_id=user_id,
        tariff_id=tariff_id,
        amount=amount,
        payment_method=payment_method,
        screenshot_file_id=screenshot_file_id,
        payment_type=payment_type,
        promo_id=promo_id,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def get_payment(session: AsyncSession, payment_id: int) -> Optional[Payment]:
    result = await session.execute(
        select(Payment)
        .options(selectinload(Payment.user), selectinload(Payment.tariff))
        .where(Payment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def update_payment(session: AsyncSession, payment_id: int, **kwargs) -> None:
    await session.execute(update(Payment).where(Payment.id == payment_id).values(**kwargs))
    await session.commit()


async def get_pending_payments(session: AsyncSession) -> list[Payment]:
    result = await session.execute(
        select(Payment)
        .options(selectinload(Payment.user), selectinload(Payment.tariff))
        .where(Payment.status == "pending")
        .order_by(Payment.created_at)
    )
    return result.scalars().all()


async def get_revenue_stats(session: AsyncSession) -> dict:
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=now.weekday())

    reset_filter = []
    reset_at_str = await get_setting(session, "revenue_reset_at", "")
    if reset_at_str:
        try:
            reset_filter = [Payment.created_at >= datetime.fromisoformat(reset_at_str)]
        except ValueError:
            pass

    base = [Payment.status == "approved"]
    total = await session.scalar(select(func.sum(Payment.amount)).where(*base, *reset_filter)) or 0
    monthly = await session.scalar(
        select(func.sum(Payment.amount)).where(*base, Payment.created_at >= month_start)
    ) or 0
    weekly = await session.scalar(
        select(func.sum(Payment.amount)).where(*base, Payment.created_at >= week_start)
    ) or 0
    return {"total": float(total), "monthly": float(monthly), "weekly": float(weekly)}


async def has_used_trial(session: AsyncSession, user_id: int) -> bool:
    # Если есть одобренный платёж по триал-тарифу — уже использовал
    trial_used = await session.scalar(
        select(func.count(Payment.id))
        .join(Tariff, Payment.tariff_id == Tariff.id)
        .where(Payment.user_id == user_id, Payment.status == "approved", Tariff.is_trial == True)
    )
    if trial_used:
        return True
    # Если уже есть uuid в панели — новичком не считается
    result = await session.execute(
        select(User.remnawave_uuid).where(User.id == user_id)
    )
    row = result.scalar_one_or_none()
    return row is not None


async def has_used_referral_tariff(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(Payment.id)
        .join(Tariff, Payment.tariff_id == Tariff.id)
        .where(Payment.user_id == user_id, Payment.status == "approved", Tariff.is_referral == True)
    )
    return result.scalar_one_or_none() is not None


async def has_any_approved_payment(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(Payment.id).where(Payment.user_id == user_id, Payment.status == "approved")
    )
    return result.scalar_one_or_none() is not None


# ── Promo codes ────────────────────────────────────────────────────────────

async def get_promo_by_code(session: AsyncSession, code: str) -> Optional[PromoCode]:
    result = await session.execute(select(PromoCode).where(PromoCode.code == code.upper()))
    return result.scalar_one_or_none()


async def validate_promo(
    session: AsyncSession, code: str, tariff_id: int
) -> tuple[Optional[PromoCode], Optional[str]]:
    promo = await get_promo_by_code(session, code)
    if not promo:
        return None, "Промокод не найден."
    if not promo.is_active:
        return None, "Промокод неактивен."
    if promo.expires_at and promo.expires_at < datetime.utcnow():
        return None, "Срок действия промокода истёк."
    if promo.used_count >= promo.max_uses:
        return None, "Промокод исчерпан."
    if promo.tariff_id and promo.tariff_id != tariff_id:
        return None, "Промокод не применяется к этому тарифу."
    return promo, None


def apply_promo_discount(promo: PromoCode, original_price: float) -> float:
    price = original_price
    if promo.discount_fixed:
        price -= float(promo.discount_fixed)
    if promo.discount_percent:
        price -= price * promo.discount_percent / 100
    return max(0.0, round(price, 2))


async def use_promo(session: AsyncSession, promo_id: int) -> None:
    await session.execute(
        update(PromoCode).where(PromoCode.id == promo_id)
        .values(used_count=PromoCode.used_count + 1)
    )
    await session.commit()


async def create_promo(session: AsyncSession, **kwargs) -> PromoCode:
    allowed = {"code", "discount_percent", "discount_fixed", "tariff_id", "max_uses", "expires_at", "is_active"}
    data = {k: v for k, v in kwargs.items() if k in allowed}
    if "code" in data:
        data["code"] = data["code"].upper()
    promo = PromoCode(**data)
    session.add(promo)
    await session.commit()
    await session.refresh(promo)
    return promo


async def get_all_promos(session: AsyncSession) -> list[PromoCode]:
    result = await session.execute(select(PromoCode).order_by(PromoCode.created_at.desc()))
    return result.scalars().all()


async def update_promo(session: AsyncSession, promo_id: int, **kwargs) -> None:
    await session.execute(update(PromoCode).where(PromoCode.id == promo_id).values(**kwargs))
    await session.commit()


async def delete_promo(session: AsyncSession, promo_id: int) -> None:
    await session.execute(delete(PromoCode).where(PromoCode.id == promo_id))
    await session.commit()


# ── Support tickets ────────────────────────────────────────────────────────

async def get_open_ticket(session: AsyncSession, user_id: int) -> Optional[SupportTicket]:
    result = await session.execute(
        select(SupportTicket).where(SupportTicket.user_id == user_id, SupportTicket.status == "open")
    )
    return result.scalar_one_or_none()


async def create_ticket(session: AsyncSession, user_id: int) -> SupportTicket:
    ticket = SupportTicket(user_id=user_id)
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


async def close_ticket(session: AsyncSession, ticket_id: int) -> None:
    await session.execute(
        update(SupportTicket).where(SupportTicket.id == ticket_id).values(status="closed")
    )
    await session.commit()


async def add_ticket_message(
    session: AsyncSession, ticket_id: int, sender_role: str, sender_tg_id: int,
    text: Optional[str] = None, media_file_id: Optional[str] = None,
    media_type: Optional[str] = None, tg_message_id: Optional[int] = None,
) -> TicketMessage:
    msg = TicketMessage(
        ticket_id=ticket_id, sender_role=sender_role, sender_tg_id=sender_tg_id,
        text=text, media_file_id=media_file_id, media_type=media_type, tg_message_id=tg_message_id,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def get_open_tickets(session: AsyncSession) -> list[SupportTicket]:
    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .where(SupportTicket.status == "open")
        .order_by(SupportTicket.updated_at)
    )
    return result.scalars().all()


async def get_closed_tickets(
    session: AsyncSession, user_id: Optional[int] = None, limit: int = 20
) -> list[SupportTicket]:
    q = (
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .where(SupportTicket.status == "closed")
    )
    if user_id:
        q = q.where(SupportTicket.user_id == user_id)
    result = await session.execute(q.order_by(SupportTicket.updated_at.desc()).limit(limit))
    return result.scalars().all()


async def get_ticket_by_id(session: AsyncSession, ticket_id: int) -> Optional[SupportTicket]:
    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user), selectinload(SupportTicket.messages))
        .where(SupportTicket.id == ticket_id)
    )
    return result.scalar_one_or_none()


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

async def was_notified(
    session: AsyncSession, user_id: int, notif_type: str, meta: Optional[str] = None
) -> bool:
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


async def log_notification(
    session: AsyncSession, user_id: int, notif_type: str, meta: Optional[str] = None
) -> None:
    session.add(Notification(user_id=user_id, type=notif_type, meta=meta))
    await session.commit()


# ── Custom menu buttons ────────────────────────────────────────────────────

async def get_active_custom_buttons(session: AsyncSession) -> list[CustomMenuButton]:
    result = await session.execute(
        select(CustomMenuButton)
        .where(CustomMenuButton.is_active == True)
        .order_by(CustomMenuButton.sort_order)
    )
    return result.scalars().all()


async def get_all_custom_buttons(session: AsyncSession) -> list[CustomMenuButton]:
    result = await session.execute(select(CustomMenuButton).order_by(CustomMenuButton.sort_order))
    return result.scalars().all()


async def create_custom_button(
    session: AsyncSession, text: str, url: str, condition: str = "all", sort_order: int = 0
) -> CustomMenuButton:
    btn = CustomMenuButton(text=text, url=url, condition=condition, sort_order=sort_order)
    session.add(btn)
    await session.commit()
    await session.refresh(btn)
    return btn


async def update_custom_button(session: AsyncSession, btn_id: int, **kwargs) -> None:
    await session.execute(
        update(CustomMenuButton).where(CustomMenuButton.id == btn_id).values(**kwargs)
    )
    await session.commit()


async def delete_custom_button(session: AsyncSession, btn_id: int) -> None:
    await session.execute(delete(CustomMenuButton).where(CustomMenuButton.id == btn_id))
    await session.commit()
