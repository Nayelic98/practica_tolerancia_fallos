#!/usr/bin/env bash
# Revierte lo hecho por flap-database.sh: vuelve a apuntar los servicios
# directo a Postgres y elimina Toxiproxy.
#
# Uso: ./scripts/chaos/restore-database.sh [namespace]

set -euo pipefail

NAMESPACE="${1:-ticketing}"

echo "==> Restaurando DB_HOST=postgres en inventario-service y reservas-service"
kubectl -n "$NAMESPACE" set env deployment/inventario-service DB_HOST=postgres
kubectl -n "$NAMESPACE" set env deployment/reservas-service DB_HOST=postgres
kubectl -n "$NAMESPACE" rollout status deployment/inventario-service --timeout=90s
kubectl -n "$NAMESPACE" rollout status deployment/reservas-service --timeout=90s

echo "==> Eliminando Toxiproxy"
kubectl delete -f k8s/chaos/toxiproxy.yaml --ignore-not-found

echo "Listo."
