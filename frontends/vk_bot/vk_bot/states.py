"""FSM-состояния VK-бота."""
from vkbottle import BaseStateGroup


class RegistrationStates(BaseStateGroup):
    WAITING_PHONE = "waiting_phone"


class CheckoutStates(BaseStateGroup):
    ENTERING_ADDRESS = "entering_address"
    ENTERING_COMMENT = "entering_comment"
