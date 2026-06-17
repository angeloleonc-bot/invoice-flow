from django.urls import path

from . import views

app_name = "management"

urlpatterns = [
    path("", views.my_work, name="index"),
    path("my-work/", views.my_work, name="my_work"),
    path("document/<int:id>/", views.document_detail, name="document_detail"),
]