#!/usr/bin/env bash
# Escenario 1: "El Inventario Fantasma"
#
# Elimina una réplica de inventario-service en pleno vuelo, para comprobar que
# reservas-service reintenta (retry con backoff ante httpx.ConnectError) y
# la Service de Kubernetes enruta a la réplica sobreviviente, repartida en el
# otro nodo físico por el podAntiAffinity de la Parte I.
#
# Correr en paralelo, en otra terminal:
#   python scripts/chaos/load_client.py http://<IP_NODO>:30080 --interval 0.3 --duration 30
#
# Uso: ./scripts/chaos/kill-inventario-pod.sh [namespace]

set -euo pipefail
NAMESPACE="${1:-ticketing}"

echo "==> Réplicas actuales de inventario-service:"
kubectl -n "$NAMESPACE" get pods -l app=inventario-service -o wide

POD=$(kubectl -n "$NAMESPACE" get pods -l app=inventario-service -o jsonpath='{.items[0].metadata.name}')
NODE=$(kubectl -n "$NAMESPACE" get pod "$POD" -o jsonpath='{.spec.nodeName}')

echo "==> Eliminando pod $POD (nodo: $NODE) — simula un crash real"
kubectl -n "$NAMESPACE" delete pod "$POD" --grace-period=0 --force

echo "==> Esperando a que el Deployment reponga la réplica..."
kubectl -n "$NAMESPACE" rollout status deployment/inventario-service --timeout=60s

echo "==> Réplicas tras la recuperación:"
kubectl -n "$NAMESPACE" get pods -l app=inventario-service -o wide
