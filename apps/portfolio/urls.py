from django.urls import path

from . import views

app_name = "portfolio"

urlpatterns = [
    path("documents/", views.documents_list, name="documents_list"),
    path("customers/", views.customers_list, name="customers_list"),
]
