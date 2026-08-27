"""Публичная веб-форма естественного заказа."""
from django import forms

from apps.customers.validators import normalize_email, normalize_phone, validate_phone


class NaturalOrderForm(forms.Form):
    submission_id = forms.UUIDField(widget=forms.HiddenInput)
    name = forms.CharField(
        label="Ваше имя",
        max_length=255,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    phone = forms.CharField(
        label="Телефон",
        max_length=32,
        required=False,
        help_text="Телефон или email обязателен. Например, +7 999 123-45-67",
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )
    email = forms.EmailField(
        label="Email",
        max_length=320,
        required=False,
        help_text="Телефон или email обязателен.",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    message = forms.CharField(
        label="Что вы хотите заказать?",
        max_length=20_000,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": (
                    "Например: две упаковки тигровых креветок, самовывоз завтра, "
                    "оплата картой онлайн"
                ),
            }
        ),
    )
    personal_data_consent = forms.BooleanField(
        label="Я согласен на обработку данных для оформления заказа",
    )

    def clean_phone(self):
        raw_phone = self.cleaned_data["phone"]
        if not raw_phone:
            return ""
        phone = normalize_phone(raw_phone)
        validate_phone(phone)
        return phone

    def clean_email(self):
        raw_email = self.cleaned_data["email"]
        return normalize_email(raw_email) if raw_email else ""

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("phone") and not cleaned_data.get("email"):
            raise forms.ValidationError("Укажите телефон или email для связи.")
        return cleaned_data
