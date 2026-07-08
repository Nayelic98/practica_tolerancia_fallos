#!/usr/bin/env bash
# Escenario 5: "El Correo Perdido"
#
# Apaga por completo notificaciones-service durante un rato y comprueba que
# reservas-service sigue confirmando reservas (fallback: notified=false) en
# vez de fallar toda la compra por un servicio no crítico.
#
# Uso: ./scripts/chaos/kill-notificaciones.sh [namespace] [duracion_seg]

set -euo pipefail
NAMESPACE="${1:-ticketing}"
DURATION="${2:-30}"

echo "==> Apagando notificaciones-service (scale a 0)"
kubectl -n "$NAMESPACE" scale deployment/notificaciones-service --replicas=0

echo "==> notificaciones-service caído durante ${DURATION}s. Prueben, por ejemplo:"
echo '    curl -X POST http://<IP_NODO>:30080/api/reservations -H "Content-Type: application/json" \'
echo '      -d "{\"event_id\":\"evt-001\",\"user_email\":\"ana@test.com\",\"quantity\":1}"'
echo "    -> debe responder 200 con \"status\": \"CONFIRMED\" y \"notified\": false"
sleep "$DURATION"

echo "==> Restaurando notificaciones-service (scale a 2)"
kubectl -n "$NAMESPACE" scale deployment/notificaciones-service --replicas=2
kubectl -n "$NAMESPACE" rollout status deployment/notificaciones-service --timeout=60s
