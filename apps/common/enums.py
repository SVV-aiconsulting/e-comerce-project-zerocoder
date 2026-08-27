"""Общие Django TextChoices для backend интернет-магазина."""
from django.db import models


class ProductUnit(models.TextChoices):
    KG = "kg", "Килограмм"
    PIECE = "piece", "Штука"
    PACKAGE = "package", "Упаковка"


class Channel(models.TextChoices):
    TELEGRAM = "telegram", "Telegram"
    VK = "vk", "ВКонтакте"
    MAX = "max", "MAX"
    WEBSITE = "website", "Сайт"
    EMAIL = "email", "Email"


class CustomerSource(models.TextChoices):
    TELEGRAM = "telegram", "Telegram"
    VK = "vk", "ВКонтакте"
    MAX = "max", "MAX"
    WEBSITE = "website", "Сайт"
    EMAIL = "email", "Email"
    PHONE = "phone", "Телефон"
    MANAGER = "manager", "Менеджер"


class CustomerStatus(models.TextChoices):
    NEW = "new", "Новый"
    ACTIVE = "active", "Активный"
    SLEEPING = "sleeping", "Спящий"
    VIP = "vip", "ВИП"
    BLACKLISTED = "blacklisted", "Чёрный список"


class CartStatus(models.TextChoices):
    ACTIVE = "active", "Активная"
    ORDERED = "ordered", "Оформлена"
    ABANDONED = "abandoned", "Брошена"


class ReceivingType(models.TextChoices):
    DELIVERY = "delivery", "Доставка"
    PICKUP = "pickup", "Самовывоз"


class TimeInterval(models.TextChoices):
    INTERVAL_10_12 = "10-12", "10:00–12:00"
    INTERVAL_12_14 = "12-14", "12:00–14:00"
    INTERVAL_14_16 = "14-16", "14:00–16:00"
    INTERVAL_16_18 = "16-18", "16:00–18:00"
    INTERVAL_18_20 = "18-20", "18:00–20:00"
    INTERVAL_20_22 = "20-22", "20:00–22:00"


class OrderStatus(models.TextChoices):
    NEW = "new", "Новый"
    ASSEMBLED = "assembled", "Собран"
    DELIVERING = "delivering", "Доставляется"
    COMPLETED = "completed", "Завершён"
    CANCELLED = "cancelled", "Отменён"


class PaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Не оплачен"
    WAITING = "waiting", "Ожидает оплаты"
    PAID = "paid", "Оплачен"


class PaymentMethod(models.TextChoices):
    CASH_ON_DELIVERY = "cash_on_delivery", "Наличные при получении"
    CARD_ON_DELIVERY = "card_on_delivery", "Карта при получении"
    CARD_PREPAYMENT = "card_prepayment", "Предоплата картой"


class StatusChangeSource(models.TextChoices):
    TELEGRAM = "telegram", "Telegram"
    VK = "vk", "ВКонтакте"
    MAX = "max", "MAX"
    DJANGO_ADMIN = "django_admin", "Админ-панель Django"
    WEBSITE = "website", "Сайт"
    EMAIL = "email", "Email"
    API = "api", "API"
    AUTOMATIC = "automatic", "Автоматически"
