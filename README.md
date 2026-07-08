# Práctica de Tolerancia a Fallos — Sistema de Reservas de Entradas

Arquitectura de 6 componentes (API Gateway, Reservas, Inventario, Pagos-stub,
Notificaciones-stub, PostgreSQL) desplegada sobre un clúster Kubernetes real de
2 nodos físicos (k3s), con `reservas-service` e `inventario-service` repartidos
con 2 réplicas — una por nodo — mediante `podAntiAffinity` obligatoria.

Diagrama y detalle del flujo REST: [docs/architecture.md](docs/architecture.md) /
[docs/architecture.svg](docs/architecture.svg).

Catálogo de fallos (Parte II) y mecanismos de resiliencia implementados (Parte III):
[docs/fallos.md](docs/fallos.md) / [docs/resilience.md](docs/resilience.md).

## Estructura del repositorio

```
services/
  api-gateway/            # punto de entrada REST
  reservas-service/       # Core: orquesta inventario + pagos + notificaciones
                           # (Circuit Breaker, Retry+backoff, Fallback)
  inventario-service/     # cupo por evento, persistido en Postgres (Retry+backoff)
  pagos-service/          # stub con latencia y fallos aleatorios
  notificaciones-service/ # stub con latencia y fallos aleatorios
db/init.sql               # esquema + datos semilla (eventos)
k8s/                      # manifiestos de Kubernetes de la arquitectura base
k8s/chaos/                # recursos usados solo para inyectar fallos (Toxiproxy)
docker-compose.yml        # para probar todo localmente antes de ir a k8s
scripts/                  # build & push de imágenes
scripts/chaos/            # scripts de inyección de fallos + cliente de carga
docs/fallos.md            # Parte II: tabla fallo -> mecanismo de inyección
docs/resilience.md        # Parte III: patrones, justificación y evidencia
```

## 1. Prueba rápida en local (sin Kubernetes)

Sirve para validar que la lógica de negocio funciona antes de pelear con la
infraestructura de dos nodos.

```bash
docker compose up --build
```

```bash
curl http://localhost:8000/api/events/evt-001
curl -X POST http://localhost:8000/api/reservations \
  -H "Content-Type: application/json" \
  -d '{"event_id":"evt-001","user_email":"ana@test.com","quantity":2}'
curl http://localhost:8000/api/reservations/1
```

Repitiendo el `POST` varias veces se observan casos `402 payment declined` y
`503`/lentitud provenientes del stub de pagos — es esperado, simula un
proveedor real.

## 2. Levantar el clúster real de 2 nodos (k3s)

Requisitos: dos computadoras en la misma red local, una con Linux (nativo o VM)
y otra con Windows + WSL2, que puedan alcanzarse por IP entre sí.

### 2.1 Nodo servidor — en la computadora Linux

```bash
curl -sfL https://get.k3s.io | sh -
sudo cat /var/lib/rancher/k3s/server/node-token   # guardar este token
ip addr show                                      # anotar la IP LAN, p.ej. 192.168.1.10
```

### 2.2 Nodo agente — en la computadora Windows (dentro de WSL2)

Instalar una distro Linux en WSL2 si no existe (`wsl --install`) y, dentro de esa
terminal Linux:

```bash
curl -sfL https://get.k3s.io | \
  K3S_URL=https://<IP_DEL_NODO_LINUX>:6443 \
  K3S_TOKEN=<TOKEN_COPIADO_EN_2.1> \
  sh -
```

Abrir en el firewall de Windows los puertos usados por k3s/flannel si no
conectan: **6443/tcp** (API server), **8472/udp** (VXLAN de flannel) y
**30080/tcp** (NodePort del API Gateway).

### 2.3 Verificar el clúster

Desde el nodo Linux (o copiando `/etc/rancher/k3s/k3s.yaml` — reemplazando
`127.0.0.1` por la IP LAN del nodo servidor — a `~/.kube/config` en la máquina
desde la que se administre):

```bash
sudo kubectl get nodes -o wide
```

Debe listar **2 nodos** en estado `Ready` (uno Linux, uno Windows/WSL2).

## 3. Construir y publicar las imágenes

Como los pods se ejecutan en dos máquinas físicas distintas, no alcanza con
`docker build` local: cada nodo necesita poder descargar (`pull`) la imagen
desde un registry accesible por ambos (Docker Hub, o cualquier registry propio
en la misma red). Requiere una cuenta gratuita en Docker Hub.

```bash
docker login
./scripts/build-and-push.sh <tu-usuario-dockerhub>
```

(En PowerShell: `./scripts/build-and-push.ps1 -Registry <tu-usuario-dockerhub>`)

Esto construye y sube las 5 imágenes, y genera `k8s/generated/*.yaml` con el
placeholder `__REGISTRY__` ya reemplazado por tu usuario.

## 4. Desplegar en el clúster

```bash
kubectl apply -f k8s/generated/00-namespace.yaml
kubectl apply -f k8s/generated/
```

Verificar que las réplicas de los servicios críticos quedaron una en cada nodo:

```bash
kubectl -n ticketing get pods -o wide
```

La columna `NODE` debe mostrar, para `reservas-service` e `inventario-service`,
un pod en cada uno de los dos nodos (si ambos cayeran en el mismo nodo, el pod
restante quedaría `Pending`: eso indicaría que el `podAntiAffinity` no se está
respetando o que falta un nodo `Ready`).

## 5. Probar el sistema desplegado

```bash
GATEWAY_IP=<IP_DE_CUALQUIERA_DE_LOS_2_NODOS>
curl http://$GATEWAY_IP:30080/api/events/evt-001
curl -X POST http://$GATEWAY_IP:30080/api/reservations \
  -H "Content-Type: application/json" \
  -d '{"event_id":"evt-001","user_email":"ana@test.com","quantity":2}'
```

Como el `Service` de `api-gateway` es `NodePort`, responde tanto si se apunta a
la IP del nodo Linux como a la del nodo Windows, independientemente de en cuál
de los dos esté corriendo el pod que atiende la petición.

## Limitación conocida

`postgres` corre con una sola réplica; su volumen (`local-path` de k3s) queda
fijado al nodo donde se programó el pod la primera vez, por lo que la caída de
ese nodo específico deja la base de datos no disponible hasta que vuelva. Es un
punto de partida documentado para las siguientes partes de la práctica
(mecanismos de resiliencia).
