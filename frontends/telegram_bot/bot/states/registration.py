from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    waiting_contact = State()


class CheckoutStates(StatesGroup):
    entering_address = State()
    entering_receipt_email = State()
    entering_comment = State()
