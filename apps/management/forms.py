from django import forms

from .models import CollectionAction


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