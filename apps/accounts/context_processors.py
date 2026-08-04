from __future__ import annotations

from typing import Any

from apps.accounts.models import Role
from apps.accounts.services.role_service import RoleService


ROLE_DISPLAY_NAMES = dict(Role.ROLE_CHOICES)


def _get_display_name(user: Any) -> str:
    """
    Devuelve el nombre visible del usuario.

    Prioridad:
    1. Nombre completo.
    2. Username.
    3. Identificador genérico.
    """
    full_name = str(user.get_full_name() or "").strip()

    if full_name:
        return full_name

    username = str(user.get_username() or "").strip()

    if username:
        return username

    return "Usuario"


def _get_initials(display_name: str) -> str:
    """
    Genera hasta dos iniciales para el avatar.
    """
    words = [
        word
        for word in str(display_name).strip().split()
        if word
    ]

    if not words:
        return "U"

    if len(words) == 1:
        return words[0][:2].upper()

    return (
        words[0][0]
        + words[-1][0]
    ).upper()


def _get_role_display_name(user: Any) -> str:
    """
    Obtiene el rol efectivo desde RoleService y lo traduce
    a su etiqueta visible.
    """
    role_code = RoleService.get_effective_role_code(user)

    if not role_code:
        return "Sin rol asignado"

    return ROLE_DISPLAY_NAMES.get(
        role_code,
        role_code.replace("_", " ").title(),
    )


def current_user_context(request) -> dict[str, str]:
    """
    Contexto visual global del usuario autenticado.

    No expone información sensible y evita duplicar lógica
    de identidad y roles en los templates.
    """
    user = getattr(request, "user", None)

    if not user or not user.is_authenticated:
        return {
            "current_user_display_name": "",
            "current_user_initials": "",
            "current_user_role_name": "",
            "current_user_email": "",
            "current_user_identifier": "",
        }

    display_name = _get_display_name(user)
    email = str(getattr(user, "email", "") or "").strip()
    username = str(user.get_username() or "").strip()

    return {
        "current_user_display_name": display_name,
        "current_user_initials": _get_initials(display_name),
        "current_user_role_name": _get_role_display_name(user),
        "current_user_email": email,
        "current_user_identifier": email or username,
    }