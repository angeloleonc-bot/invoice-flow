from __future__ import annotations

import logging

import msal

from cryptography.fernet import (
    Fernet,
    InvalidToken,
)
from django.conf import settings


logger = logging.getLogger(__name__)


SESSION_KEY = "entra_delegated_token_cache"


class DelegatedTokenCacheError(Exception):
    """
    Error restaurando un cache delegado previamente almacenado.
    """


class DelegatedTokenCacheService:
    """
    Gestiona el SerializableTokenCache delegado de Microsoft.

    Seguridad:
    - nunca guarda tokens en modelos de negocio;
    - nunca escribe tokens en logs;
    - si existe clave Fernet, persiste el cache cifrado
      dentro de la sesión Django;
    - si no existe clave, el cache funciona solamente
      durante la petición actual y no se persiste.

    Esta última conducta permite desarrollo local sin secreto
    adicional y evita almacenar tokens en texto claro.
    """

    @classmethod
    def encryption_enabled(cls) -> bool:
        return bool(
            str(
                getattr(
                    settings,
                    "ENTRA_TOKEN_CACHE_ENCRYPTION_KEY",
                    "",
                )
                or ""
            ).strip()
        )

    @classmethod
    def _fernet(cls) -> Fernet:
        raw_key = str(
            getattr(
                settings,
                "ENTRA_TOKEN_CACHE_ENCRYPTION_KEY",
                "",
            )
            or ""
        ).strip()

        if not raw_key:
            raise DelegatedTokenCacheError(
                "El cifrado del cache delegado no está habilitado."
            )

        try:
            return Fernet(
                raw_key.encode("ascii")
            )
        except Exception as exc:
            raise DelegatedTokenCacheError(
                "ENTRA_TOKEN_CACHE_ENCRYPTION_KEY "
                "no contiene una clave Fernet válida."
            ) from exc

    @classmethod
    def create_empty_cache(
        cls,
    ) -> msal.SerializableTokenCache:
        return msal.SerializableTokenCache()

    @classmethod
    def load_from_session(
        cls,
        session,
    ) -> msal.SerializableTokenCache:
        """
        Devuelve siempre un SerializableTokenCache.

        Sin clave de cifrado:
        - no intenta restaurar nada;
        - elimina cualquier blob antiguo;
        - devuelve cache vacío.
        """

        cache = msal.SerializableTokenCache()

        if not cls.encryption_enabled():
            session.pop(
                SESSION_KEY,
                None,
            )
            return cache

        encrypted = str(
            session.get(
                SESSION_KEY,
                "",
            )
            or ""
        ).strip()

        if not encrypted:
            return cache

        try:
            serialized = (
                cls._fernet()
                .decrypt(
                    encrypted.encode("ascii")
                )
                .decode("utf-8")
            )

            cache.deserialize(
                serialized
            )

        except InvalidToken as exc:
            session.pop(
                SESSION_KEY,
                None,
            )
            session.modified = True

            raise DelegatedTokenCacheError(
                "El cache delegado de Microsoft "
                "no pudo ser descifrado."
            ) from exc

        except DelegatedTokenCacheError:
            raise

        except Exception as exc:
            session.pop(
                SESSION_KEY,
                None,
            )
            session.modified = True

            raise DelegatedTokenCacheError(
                "El cache delegado de Microsoft "
                "no pudo ser restaurado."
            ) from exc

        return cache

    @classmethod
    def save_to_session(
        cls,
        *,
        session,
        token_cache: msal.SerializableTokenCache,
    ) -> bool:
        """
        Retorna True cuando el cache fue persistido.

        Si no existe clave:
        - NO persiste el cache;
        - NO falla el login;
        - retorna False.
        """

        if not cls.encryption_enabled():
            session.pop(
                SESSION_KEY,
                None,
            )
            return False

        serialized = token_cache.serialize()

        if not serialized:
            session.pop(
                SESSION_KEY,
                None,
            )
            session.modified = True
            return False

        encrypted = (
            cls._fernet()
            .encrypt(
                serialized.encode("utf-8")
            )
            .decode("ascii")
        )

        session[
            SESSION_KEY
        ] = encrypted

        session.modified = True

        return True

    @classmethod
    def clear_session(
        cls,
        session,
    ) -> None:
        session.pop(
            SESSION_KEY,
            None,
        )
        session.modified = True
