"""Единый формат ошибок REST API."""
from rest_framework import status
from rest_framework.exceptions import APIException, AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.common.exceptions import (
    CartEmptyError,
    CartNotAvailableError,
    ChannelIdentityAlreadyLinkedError,
    DeliveryError,
    MinQuantityError,
    ProductUnavailableError,
    ShopError,
)
from apps.payments.exceptions import PaymentError, YooKassaAPIError


def _error_response(code: str, message: str, details: dict | None = None, http_status=400):
    return Response(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            }
        },
        status=http_status,
    )


def _map_shop_error(exc: ShopError) -> Response:
    if isinstance(exc, YooKassaAPIError):
        return _error_response(
            "payment_provider_error",
            str(exc),
            {"provider_code": exc.code},
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.retryable
            else status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, PaymentError):
        return _error_response(
            "payment_error",
            str(exc),
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, MinQuantityError):
        return _error_response(
            "invalid_quantity",
            str(exc),
            {"product_name": exc.product_name, "min_quantity": str(exc.min_quantity)},
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, ProductUnavailableError):
        return _error_response("product_unavailable", str(exc), http_status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    if isinstance(exc, CartEmptyError):
        return _error_response("empty_cart", str(exc), http_status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    if isinstance(exc, DeliveryError):
        return _error_response("delivery_error", str(exc), http_status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    if isinstance(exc, ChannelIdentityAlreadyLinkedError):
        return _error_response("channel_identity_conflict", str(exc), http_status=status.HTTP_409_CONFLICT)
    if isinstance(exc, CartNotAvailableError):
        message = str(exc)
        code = "cart_customer_mismatch" if "другому клиенту" in message else "cart_already_ordered"
        return _error_response(code, message, http_status=status.HTTP_409_CONFLICT)
    return _error_response("shop_error", str(exc), http_status=status.HTTP_400_BAD_REQUEST)


def _normalize_validation_error(exc: ValidationError) -> Response:
    if isinstance(exc.detail, dict):
        return _error_response("validation_error", "Ошибка валидации данных", exc.detail)
    if isinstance(exc.detail, list):
        return _error_response("validation_error", "Ошибка валидации данных", {"messages": exc.detail})
    return _error_response("validation_error", str(exc.detail))


def shop_exception_handler(exc, context):
    if isinstance(exc, ShopError):
        return _map_shop_error(exc)

    if isinstance(exc, AuthenticationFailed):
        return _error_response("authentication_failed", str(exc.detail), http_status=status.HTTP_401_UNAUTHORIZED)

    if isinstance(exc, PermissionDenied):
        return _error_response("permission_denied", str(exc.detail), http_status=status.HTTP_403_FORBIDDEN)

    response = exception_handler(exc, context)
    if response is None:
        return response

    if isinstance(exc, ValidationError):
        return _normalize_validation_error(exc)

    if isinstance(exc, APIException):
        code = getattr(exc, "default_code", "api_error")
        if hasattr(code, "value"):
            code = code.value
        return _error_response(str(code), str(exc.detail), http_status=exc.status_code)

    return response


class ProductNotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Товар не найден"
    default_code = "product_not_found"


class ProductInactive(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Товар недоступен"
    default_code = "product_inactive"


class CustomerNotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Клиент не найден"
    default_code = "customer_not_found"


class OrderNotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Заказ не найден"
    default_code = "order_not_found"


class CustomerContextMismatch(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "customer_id не соответствует channel и external_user_id"
    default_code = "customer_context_mismatch"


class CustomerIdentityRequired(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Клиент не идентифицирован для данного канала"
    default_code = "customer_identity_required"


class OrderAccessDenied(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Нет доступа к заказу"
    default_code = "order_access_denied"
