#!/usr/bin/env bash
# Escenario 2: "La Pasarela Lenta"
#
# Fuerza a pagos-service a tardar SLOW_MS en el 100% de los cobros, simulando
# una pasarela sobrecargada, y lo revierte automáticamente al terminar.
# Sirve para observar cómo el circuit breaker de reservas-service abre tras
# unos pocos fallos y empieza a responder rápido (sin llamar a la pasarela)
# mientras dura la degradación.
#
# Correr en paralelo, en otra terminal:
#   python scripts/chaos/load_client.py http://<IP_NODO>:30080 --interval 1 --duration 90
#   kubectl -n ticketing logs -l app=reservas-service -f
#
# Uso: ./scripts/chaos/slow-payment-gateway.sh [namespace] [duracion_seg] [slow_ms]

set -euo pipefail
NAMESPACE="${1:-ticketing}"
DURATION="${2:-60}"
SLOW_MS="${3:-20000}"

echo "==> Forzando pagos-service a responder en ${SLOW_MS}ms en el 100% de los cobros"
kubectl -n "$NAMESPACE" set env deployment/pagos-service TIMEOUT_RATE=1 SLOW_TIMEOUT_MS="$SLOW_MS"
kubectl -n "$NAMESPACE" rollout status deployment/pagos-service --timeout=60s

echo "==> Pasarela lenta activa durante ${DURATION}s."
echo "    Observen en los logs de reservas-service la transición del circuit breaker:"
echo "    kubectl -n $NAMESPACE logs -l app=reservas-service -f | grep -i breaker"
sleep "$DURATION"

echo "==> Restaurando pagos-service a su comportamiento normal"
kubectl -n "$NAMESPACE" set env deployment/pagos-service TIMEOUT_RATE=0.05 SLOW_TIMEOUT_MS=8000
kubectl -n "$NAMESPACE" rollout status deployment/pagos-service --timeout=60s

echo "==> Nota: el circuit breaker vuelve a cerrar solo (half-open) pasado CB_RESET_TIMEOUT (30s por defecto)"
