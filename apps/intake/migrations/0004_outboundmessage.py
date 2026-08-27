import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intake", "0003_alter_orderdraftitem_resolution_source"),
    ]

    operations = [
        migrations.CreateModel(
            name="OutboundMessage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("telegram", "Telegram"),
                            ("vk", "ВКонтакте"),
                            ("max", "MAX"),
                            ("website", "Сайт"),
                            ("email", "Email"),
                        ],
                        max_length=16,
                    ),
                ),
                ("recipient", models.CharField(max_length=320)),
                ("response_id", models.CharField(max_length=255)),
                ("subject", models.CharField(max_length=998)),
                ("body", models.TextField()),
                ("headers", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Ожидает отправки"),
                            ("sending", "Отправляется"),
                            ("retry_scheduled", "Ожидает повтора"),
                            ("sent", "Отправлено"),
                            ("failed", "Ошибка"),
                        ],
                        default="pending",
                        max_length=24,
                    ),
                ),
                ("delivery_attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "processing_token",
                    models.UUIDField(blank=True, editable=False, null=True),
                ),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("next_retry_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("provider_message_id", models.CharField(blank=True, max_length=255)),
                ("last_error", models.TextField(blank=True)),
                (
                    "event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outbound_message",
                        to="intake.inboundevent",
                        verbose_name="Входящее событие",
                    ),
                ),
            ],
            options={
                "verbose_name": "Исходящее сообщение",
                "verbose_name_plural": "Исходящие сообщения",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "next_retry_at"],
                        name="intake_outbound_queue_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("channel", "recipient", "response_id"),
                        name="intake_unique_outbound_response",
                    )
                ],
            },
        ),
    ]
