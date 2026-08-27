import pytest

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.services.error_messages import user_message_for_error


def test_error_formatting_backend_unavailable():
    msg = user_message_for_error(BackendUnavailableError())
    assert "недоступен" in msg.lower()


def test_error_formatting_invalid_phone():
    exc = ApiError(
        code="validation_error",
        message="Ошибка",
        details={"phone": ["bad"]},
    )
    msg = user_message_for_error(exc)
    assert "79991234567" in msg


def test_vk_handlers_do_not_import_django():
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "vk_bot"
    forbidden = ("django", "apps.")
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden), alias.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden), node.module
