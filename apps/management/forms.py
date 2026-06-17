from django import forms

from .models import CollectionAction, PaymentPromise


class CollectionActionForm(forms.ModelForm):
    class Meta:
        model = CollectionAction
        fields = [
            "action_type",
            "title",
            "description",
        ]

        widgets = {
            "action_type": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Título de la gestión",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Detalle de la gestión realizada",
                }
            ),
        }


class PaymentPromiseForm(forms.ModelForm):
    class Meta:
        model = PaymentPromise
        fields = [
            "promise_date",
            "promised_amount",
            "notes",
        ]

        widgets = {
            "promise_date": forms.DateInput(
                attrs={
                    "class": "form-control",
                    "type": "date",
                }
            ),
            "promised_amount": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "Monto comprometido",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Notas de la promesa de pago",
                }
            ),
        }