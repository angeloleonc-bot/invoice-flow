from django.urls import path

from . import views

app_name = "management"

urlpatterns = [
    path("", views.my_work, name="index"),
    path("my-work/", views.my_work, name="my_work"),
    path("document/<int:id>/", views.document_detail, name="document_detail"),
    path("alerts/", views.alerts_center, name="alerts_center"),
    path("alerts/<int:alert_id>/resolve/", views.alert_resolve, name="alert_resolve"),
    path("alerts/<int:alert_id>/postpone/", views.alert_postpone, name="alert_postpone"),
    path("alerts/<int:alert_id>/dismiss/", views.alert_dismiss, name="alert_dismiss"),
]