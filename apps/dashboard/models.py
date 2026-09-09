from django.db import models

from apps.orders.models import Order


class AnalyticsDashboard(Order):
    """Admin navigation entry for the manager analytics page."""

    class Meta:
        proxy = True
        verbose_name = "Статистика и аналитика"
        verbose_name_plural = "Статистика и аналитика"
