#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

PROJECT_DIR="${INVOICE_FLOW_PROJECT_DIR:?INVOICE_FLOW_PROJECT_DIR no está configurado}"
COMPOSE_FILE="${INVOICE_FLOW_COMPOSE_FILE:-compose.yml}"
DJANGO_SERVICE="${INVOICE_FLOW_DJANGO_SERVICE:-web}"

LOCK_FILE="${INVOICE_FLOW_DOCUMENT_SUPPORT_SYNC_LOCK_FILE:-/run/lock/invoice-flow-document-support-sync.lock}"

ENV_FILE="${INVOICE_FLOW_DOCUMENT_SUPPORT_ENV_FILE:-/etc/invoice-flow/document-support-sync.env}"

VERBOSITY="${INVOICE_FLOW_DOCUMENT_SUPPORT_SYNC_VERBOSITY:-1}"

STARTED_AT="$(date +%s)"

log() {
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

on_error() {
    local exit_code="$?"
    local line_number="${1:-unknown}"
    local elapsed

    elapsed="$(( $(date +%s) - STARTED_AT ))"

    log "DOCUMENT_SUPPORT_SYNC RESULT=ERROR EXIT_CODE=${exit_code} LINE=${line_number} DURATION_SECONDS=${elapsed}"

    exit "${exit_code}"
}

trap 'on_error "${LINENO}"' ERR

log "===================================================================="
log "DOCUMENT_SUPPORT_SYNC RESULT=STARTED"
log "PROJECT_DIR=${PROJECT_DIR}"
log "COMPOSE_FILE=${COMPOSE_FILE}"
log "DJANGO_SERVICE=${DJANGO_SERVICE}"
log "ENV_FILE=${ENV_FILE}"
log "===================================================================="

cd "${PROJECT_DIR}"

if [[ ! -f "${COMPOSE_FILE}" ]]; then
    log "ERROR: No existe el archivo Docker Compose: ${PROJECT_DIR}/${COMPOSE_FILE}"
    exit 66
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    log "ERROR: No existe el archivo de variables del proceso: ${ENV_FILE}"
    exit 66
fi

if ! command -v docker >/dev/null 2>&1; then
    log "ERROR: docker no está disponible en PATH."
    exit 69
fi

if ! docker compose version >/dev/null 2>&1; then
    log "ERROR: docker compose no está disponible."
    exit 69
fi

if ! command -v flock >/dev/null 2>&1; then
    log "ERROR: flock no está disponible."
    exit 69
fi

LOCK_DIRECTORY="$(dirname "${LOCK_FILE}")"

if [[ ! -d "${LOCK_DIRECTORY}" ]]; then
    log "ERROR: No existe el directorio para el archivo de bloqueo: ${LOCK_DIRECTORY}"
    exit 73
fi

if [[ ! -w "${LOCK_DIRECTORY}" ]]; then
    log "ERROR: Sin permisos de escritura en el directorio de bloqueo: ${LOCK_DIRECTORY}"
    exit 73
fi

exec 9>"${LOCK_FILE}"

if ! flock -n 9; then
    log "DOCUMENT_SUPPORT_SYNC RESULT=SKIPPED REASON=ALREADY_RUNNING"
    exit 0
fi

if ! docker compose \
    -f "${COMPOSE_FILE}" \
    ps \
    --status running \
    --services |
    grep -Fxq "${DJANGO_SERVICE}"; then

    log "ERROR: El servicio Docker '${DJANGO_SERVICE}' no está en ejecución."
    exit 69
fi

log "Ejecutando sincronización de respaldos documentales."

docker compose \
    -f "${COMPOSE_FILE}" \
    run \
    --rm \
    --no-deps \
    --env-from-file "${ENV_FILE}" \
    "${DJANGO_SERVICE}" \
    python manage.py sync_document_supports \
    --verbosity "${VERBOSITY}" \
    --no-color

ELAPSED="$(( $(date +%s) - STARTED_AT ))"

log "===================================================================="
log "DOCUMENT_SUPPORT_SYNC RESULT=SUCCESS EXIT_CODE=0 DURATION_SECONDS=${ELAPSED}"
log "===================================================================="