"""Сериализаторы идентификации клиента."""
from rest_framework import serializers

from apps.common.enums import Channel


class IdentifyCustomerRequestSerializer(serializers.Serializer):
    """Запрос на идентификацию клиента по каналу и телефону."""

    channel = serializers.ChoiceField(choices=Channel.values)
    external_user_id = serializers.CharField(max_length=128)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    email = serializers.EmailField(max_length=320, required=False, allow_blank=True)
    username = serializers.CharField(max_length=255, required=False, allow_blank=True)
    display_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    phone_verified = serializers.BooleanField(required=False, default=False)
    phone_verification_source = serializers.ChoiceField(
        choices=("platform_contact", "sms_otp", "manual_input"),
        required=False,
        default="manual_input",
    )


class IdentifyCustomerResponseSerializer(serializers.Serializer):
    """Ответ с результатом идентификации клиента."""

    status = serializers.ChoiceField(
        choices=("identified", "registration_required", "conflict")
    )
    customer_id = serializers.IntegerField(required=False)
    customer_public_code = serializers.CharField(required=False)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    display_name = serializers.CharField(required=False, allow_blank=True)
    channel = serializers.ChoiceField(choices=Channel.values, required=False)
    external_user_id = serializers.CharField(required=False, allow_blank=True)
    is_new_customer = serializers.BooleanField(default=False)
    channel_linked = serializers.BooleanField(default=False)
    registration_required = serializers.BooleanField(default=False)
    next_action = serializers.CharField(required=False, allow_blank=True)
