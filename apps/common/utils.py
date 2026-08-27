"""Общие вспомогательные утилиты."""
import uuid
from typing import Callable


def generate_public_code(check_exists: Callable[[str], bool], length: int = 8) -> str:
    """Сгенерировать короткий уникальный публичный код на основе UUID hex."""
    while True:
        code = uuid.uuid4().hex[:length].upper()
        if not check_exists(code):
            return code


def generate_public_number(check_exists: Callable[[str], bool]) -> str:
    """Сгенерировать уникальный публичный номер заказа."""
    return generate_public_code(check_exists, length=10)
