# Invoice Flow

Invoice Flow es una plataforma centralizada de gestión de cobranza.

## Stack inicial

- Django
- Django Templates
- HTMX
- Tabler UI
- SQL Server como fuente externa futura
- AWS S3 para adjuntos futuros
- Git
- VSCode

## Ramas oficiales

- `dev`: desarrollo activo
- `prod`: versión estable

## Setup local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py runserver