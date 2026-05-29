from aiogram.fsm.state import State, StatesGroup


class RegistrationSG(StatesGroup):
    choose_username = State()   # ввод имени если нет @username


class PaymentSG(StatesGroup):
    choose_tariff = State()
    choose_requisite = State()
    waiting_screenshot = State()


class SupportSG(StatesGroup):
    waiting_message = State()   # пользователь пишет в поддержку


class AdminSG(StatesGroup):
    # Поддержка
    replying_ticket = State()   # admin отвечает на тикет (хранит ticket_id в data)

    # Тарифы
    tariff_name = State()
    tariff_description = State()
    tariff_days = State()
    tariff_traffic = State()
    tariff_devices = State()
    tariff_price = State()

    # Рассылка
    broadcast_text = State()

    # Тех. работы — текст уведомления
    maintenance_text = State()
