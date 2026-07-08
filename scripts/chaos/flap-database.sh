#!/usr/bin/env bash
# Escenario 4: "Base de Datos Intermitente"
#
# Despliega Toxiproxy delante de Postgres, redirige inventario-service y
# reservas-service para que hablen con Postgres A TRAVÉS de Toxiproxy, y luego
# corta/restablece la conexión en ciclos (flapping) usando la API de Toxiproxy.
#
# Mientras este script corre, ejecutar en otra terminal un cliente de carga
# (ver scripts/chaos/load_client.py) contra POST /api/reservations para
# generar tráfico real y observar en los logs de ambos servicios los
# reintentos con backoff ante psycopg2.OperationalError.
#
# Uso: ./scripts/chaos/flap-database.sh [namespace] [ciclos] [seg_caido] [seg_arriba]

set -euo pipefail

NAMESPACE="${1:-ticketing}"
CYCLES="${2:-6}"
DOWN_SECONDS="${3:-5}"
UP_SECONDS="${4:-10}"

echo "==> Desplegando Toxiproxy en el namespace '$NAMESPACE'"
kubectl apply -f k8s/chaos/toxiproxy.yaml
kubectl -n "$NAMESPACE" rollout status deployment/toxiproxy --timeout=90s

echo "==> Redirigiendo inventario-service y reservas-service a Postgres vía Toxiproxy"
kubectl -n "$NAMESPACE" set env deployment/inventario-service DB_HOST=toxiproxy
kubectl -n "$NAMESPACE" set env deployment/reservas-service DB_HOST=toxiproxy
kubectl -n "$NAMESPACE" rollout status deployment/inventario-service --timeout=90s
kubectl -n "$NAMESPACE" rollout status deployment/reservas-service --timeout=90s

echo "==> Abriendo port-forward a la API de administración de Toxiproxy (8474)"
kubectl -n "$NAMESPACE" port-forward svc/toxiproxy 8474:8474 >/tmp/toxiproxy-pf.log 2>&1 &
PF_PID=$!
trap 'echo "==> Cerrando port-forward"; kill $PF_PID 2>/dev/null || true' EXIT

sleep 2 # dar tiempo a que el port-forward levante

echo "==> Iniciando flapping: $CYCLES ciclos de ${DOWN_SECONDS}s caído / ${UP_SECONDS}s arriba"
for i in $(seq 1 "$CYCLES"); do
  echo "  [$i/$CYCLES] cortando conexión a Postgres..."
  curl -sf -X POST -H "Content-Type: application/json" \
    -d '{"enabled": false}' http://localhost:8474/proxies/postgres >/dev/null
  sleep "$DOWN_SECONDS"

  echo "  [$i/$CYCLES] restableciendo conexión a Postgres..."
  curl -sf -X POST -H "Content-Type: application/json" \
    -d '{"enabled": true}' http://localhost:8474/proxies/postgres >/dev/null
  sleep "$UP_SECONDS"
done

echo "==> Flapping terminado. Para volver al estado normal ejecutar:"
echo "    ./scripts/chaos/restore-database.sh $NAMESPACE"
