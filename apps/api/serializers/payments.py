from rest_framework import serializers

from apps.payments.models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    state_label = serializers.CharField(source="get_state_display", read_only=True)

    class Meta:
        model = Payment
        fields = (
            "id",
            "provider",
            "environment",
            "state",
            "state_label",
            "amount",
            "currency",
            "confirmation_url",
            "expires_at",
            "paid_at",
            "created_at",
        )

