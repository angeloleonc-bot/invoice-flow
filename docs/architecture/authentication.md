# Arquitectura de autenticación, identidad y seguridad base

## Estado

Etapa 03.

Esta etapa define la arquitectura base para autenticación corporativa futura, usuarios locales, roles, auditoría y seguridad de sesión.

No implementa login funcional, Azure AD, OAuth2 ni OpenID Connect.

## Decisión de modelo de usuario

Se adopta `AbstractUser`.

Motivos:

- mantiene compatibilidad con Django Admin;
- conserva permisos y grupos nativos;
- reduce complejidad inicial;
- permite extender campos propios;
- facilita una futura integración con Azure AD / Entra ID;
- evita crear un sistema de autenticación completo desde cero.

No se adopta `AbstractBaseUser` porque no existe todavía una necesidad de reemplazar completamente el comportamiento estándar de Django.

## Identidad

El usuario local será la entidad persistente principal dentro de Invoice Flow.

En etapas futuras podrá vincularse con Azure AD / Entra ID mediante:

- `external_id`;
- `identity_provider`;
- `is_identity_active`.

## Autenticación futura

La autenticación externa prevista será:

- Azure AD / Entra ID;
- OAuth2;
- OpenID Connect.

La sesión seguirá siendo local Django.

## Sesión

Política definida:

- timeout por inactividad: 30 minutos;
- timeout absoluto: 10 horas;
- logout solo local.

La implementación efectiva del middleware queda fuera de esta etapa.

## Roles oficiales

Roles definidos:

- Administrador;
- Supervisor;
- Cobrador;
- Consulta / Auditoría.

## Estrategia de permisos

La estrategia será híbrida:

1. Roles funcionales propios mediante modelo `Role`.
2. Compatibilidad con permisos nativos de Django.
3. Validación efectiva en futuras capas de servicios, vistas o middleware.
4. Auditoría obligatoria de accesos denegados.

En esta etapa no se aplican permisos efectivos.

## Auditoría

Modelo base: `AuditLog`.

Eventos mínimos definidos:

- LOGIN_SUCCESS
- LOGIN_FAILED
- LOGOUT
- SESSION_EXPIRED
- TOKEN_EXPIRED
- IDENTITY_ERROR
- USER_DISABLED
- ACCESS_DENIED

## Estructura futura

Módulos reservados:

- `apps/accounts/adapters`
- `apps/accounts/services`
- `apps/security/middleware`
- `apps/security/services`
- `apps/audit/services`

## Fuera de alcance

- login funcional;
- formularios de login;
- templates de login;
- Azure AD real;
- OAuth2 real;
- OpenID Connect real;
- middleware real de sesión;
- permisos efectivos;
- protección de rutas.