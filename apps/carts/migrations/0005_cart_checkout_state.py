from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("carts", "0004_alter_cart_channel")]

    operations = [
        migrations.AddField(model_name="cart", name="receiving_type", field=models.CharField(blank=True, choices=[("delivery", "Доставка"), ("pickup", "Самовывоз")], max_length=16, verbose_name="Способ получения текущего оформления")),
        migrations.AddField(model_name="cart", name="delivery_address", field=models.TextField(blank=True, verbose_name="Адрес текущего оформления")),
        migrations.AddField(model_name="cart", name="payment_method", field=models.CharField(blank=True, choices=[("cash_on_delivery", "Наличные при получении"), ("card_on_delivery", "Карта при получении"), ("card_prepayment", "Предоплата картой")], max_length=32, verbose_name="Способ оплаты текущего оформления")),
        migrations.AddField(model_name="cart", name="customer_comment", field=models.TextField(blank=True, verbose_name="Комментарий текущего оформления")),
        migrations.AddField(model_name="cart", name="contact_phone", field=models.CharField(blank=True, max_length=11)),
        migrations.AddField(model_name="cart", name="contact_email", field=models.EmailField(blank=True, max_length=320)),
    ]
