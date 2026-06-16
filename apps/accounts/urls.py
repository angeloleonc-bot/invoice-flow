from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("start/", views.auth_start_view, name="auth_start"),
    path(
        "callback-placeholder/",
        views.callback_placeholder_view,
        name="callback_placeholder",
    ),
    path("dev-login/", views.dev_login_view, name="dev_login"),
]