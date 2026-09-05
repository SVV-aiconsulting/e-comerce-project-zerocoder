from rest_framework import serializers

from apps.common.enums import Channel
from apps.privacy.models import ConsentStatus


class ConsentIdentitySerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=(Channel.TELEGRAM, Channel.VK, Channel.MAX))
    external_user_id = serializers.CharField(max_length=128)


class ConsentActionSerializer(ConsentIdentitySerializer):
    action = serializers.ChoiceField(
        choices=(ConsentStatus.GRANTED, ConsentStatus.DECLINED, ConsentStatus.WITHDRAWN)
    )
    source = serializers.CharField(max_length=64, default="bot_consent_button")
