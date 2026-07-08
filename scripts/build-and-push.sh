#!/usr/bin/env bash
# Construye y publica las 5 imágenes en un registry (Docker Hub u otro),
# y genera k8s/generated/*.yaml con __REGISTRY__ ya sustituido.
#
# Uso: ./scripts/build-and-push.sh <usuario-o-registry>
# Ejemplo: ./scripts/build-and-push.sh miusuario
#   -> construye miusuario/api-gateway:latest, etc.

set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "Uso: $0 <usuario-dockerhub-o-registry>"
  exit 1
fi

REGISTRY="$1"
SERVICES=(api-gateway reservas-service inventario-service pagos-service notificaciones-service)

for svc in "${SERVICES[@]}"; do
  echo "==> Construyendo $REGISTRY/$svc:latest"
  docker build -t "$REGISTRY/$svc:latest" "./services/$svc"
  echo "==> Subiendo $REGISTRY/$svc:latest"
  docker push "$REGISTRY/$svc:latest"
done

echo "==> Generando manifiestos con el registry sustituido en k8s/generated/"
mkdir -p k8s/generated
for f in k8s/*.yaml; do
  sed "s#__REGISTRY__#$REGISTRY#g" "$f" > "k8s/generated/$(basename "$f")"
done

echo "Listo. Aplica con: kubectl apply -f k8s/generated/"
