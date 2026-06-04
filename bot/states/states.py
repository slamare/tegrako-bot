from aiogram.fsm.state import State, StatesGroup


class RegistrationSG(StatesGroup):
    choose_username = State()


class PaymentSG(StatesGroup):
    choose_tariff = State()
    choose_requisite = State()
    waiting_screenshot = State()
    enter_promo = State()


class SupportSG(StatesGroup):
    waiting_message = State()


class AdminSG(StatesGroup):
    # Поддержка
    replying_ticket = State()

    # Тарифы
    tariff_name = State()
    tariff_description = State()
    tariff_days = State()
    tariff_traffic = State()
    tariff_devices = State()
    tariff_price = State()
    tariff_squad = State()
    tariff_trial = State()
    tariff_referral = State()

    # Промокоды
    promo_code = State()
    promo_discount = State()
    promo_max_uses = State()

    # Рассылка
    broadcast_text = State()

    # Тех. работы
    maintenance_text = State()

    # Реферальная система
    set_referral_days = State()

    # Поиск пользователей
    search_user = State()

    # Кастомные кнопки меню
    custbtn_text = State()
    custbtn_url = State()
    custbtn_condition = State()
