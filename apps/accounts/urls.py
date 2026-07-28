from django.urls import path

from . import views


app_name = "accounts"


urlpatterns = [
    path(
        "login/",
        views.login_view,
        name="login",
    ),
    path(
        "auth/entra/",
        views.entra_login_view,
        name="entra_login",
    ),
    path(
        "auth/callback/",
        views.entra_callback_view,
        name="entra_callback",
    ),
    path(
        "dev-login/",
        views.dev_login_view,
        name="dev_login",
    ),
    path(
        "logout/",
        views.logout_view,
        name="logout",
    ),
]