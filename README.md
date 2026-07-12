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

Requisitos: dos computadoras, una con Linux (nativo o VM) y otra con Windows +
WSL2, que puedan alcanzarse por IP entre sí.

**Si ambas están en la misma red local (LAN/WiFi)**, se pueden usar directamente
las IPs de esa red y saltar a 2.1. **Si están en redes distintas** (como en
nuestro caso — dos casas distintas), hace falta una malla VPN tipo Tailscale
primero; ver [2.0](#20-si-las-dos-máquinas-no-comparten-red-tailscale) antes de
continuar.

### 2.0 Si las dos máquinas no comparten red (Tailscale)

1. Instalar Tailscale en ambas máquinas (https://tailscale.com/download). En la
   de Windows, además, instalarlo **también dentro de WSL2** (`curl -fsSL
   https://tailscale.com/install.sh | sh` dentro de la distro) — el Tailscale
   del host Windows no es visible desde la red interna de WSL2, así que WSL2
   necesita su propia instancia para que el agente de k3s pueda usarla.
2. **Las dos máquinas deben quedar como miembros plenos de la misma tailnet**,
   no como "dispositivo compartido" entre tailnets separadas. Si cada uno ya
   tenía Tailscale instalado con su propia cuenta, usar **Invite external
   users** desde `https://login.tailscale.com/admin/users` (no "compartir
   dispositivo") y que el invitado acepte, sea aprobado por el admin, y recién
   ahí corra `tailscale login` en su máquina — si no ve un selector de
   tailnet/organización durante el login y va directo a su propia red, algo
   quedó mal (había ya iniciado sesión antes de ser aprobado, por ejemplo).
   Verificar en `https://login.tailscale.com/admin/machines` que **ambos
   dispositivos aparecen sin ninguna etiqueta "Shared in"**.
3. **Desactivar "Shields Up"** en ambas máquinas (bloquea conexiones entrantes
   de otros peers, rompe totalmente el tráfico pod-a-pod aunque el `ping` de
   Tailscale siga funcionando):
   ```bash
   sudo tailscale set --shields-up=false
   ```
4. **En la máquina Windows, activar el modo de red "mirrored" de WSL2** (el
   modo NAT por defecto de WSL2 tiene un límite de MTU efectivo bajo que rompe
   handshakes TLS/paquetes TCP medianos-grandes — síntoma típico: `ping`/paquetes
   chicos funcionan perfecto, pero cualquier conexión real se cuelga o tarda
   muchísimo). Crear/editar `%UserProfile%\.wslconfig`:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   y reiniciar WSL (`wsl --shutdown` desde PowerShell, después volver a abrir
   la distro).
5. Con eso debería alcanzar, pero si persisten cuelgues en handshakes TLS
   grandes (por ejemplo al descargar el instalador de k3s, o al autenticar con
   certificado de cliente contra el API server), el MTU de la interfaz
   principal de WSL2 puede seguir siendo bajo (`ip link show` — buscar el MTU
   de `eth0`). Mitigación: forzar un MSS más chico en las conexiones salientes:
   ```bash
   sudo iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1180
   sudo iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1180
   ```
   Estas reglas no sobreviven un reinicio de WSL — para que persistan, agregar
   un `command=` en la sección `[boot]` de `/etc/wsl.conf` (dentro de la
   distro) que las vuelva a aplicar en cada arranque:
   ```ini
   [boot]
   systemd=true
   command="iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1180; iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1180"
   ```

Con eso, las dos máquinas deberían verse entre sí igual que si estuvieran en la
misma LAN (`ping <IP_TAILSCALE_DEL_OTRO>` responde rápido y estable) — seguir
con 2.1 usando las IPs de Tailscale (`100.x.y.z`) en vez de IPs de LAN.

### 2.1 Nodo servidor — en la computadora Linux

```bash
curl -sfL https://get.k3s.io | sh -s - --node-ip=<IP_PROPIA> --flannel-iface=<INTERFAZ>
sudo cat /var/lib/rancher/k3s/server/node-token   # guardar este token
```
`--node-ip`/`--flannel-iface` solo son necesarios si se usa Tailscale (paso
2.0) — con ambas máquinas en la misma LAN alcanza con `curl -sfL
https://get.k3s.io | sh -`. Con Tailscale, `<IP_PROPIA>` es la IP `100.x.y.z`
de esta máquina y `<INTERFAZ>` es `tailscale0`.

### 2.2 Nodo agente — en la computadora Windows (dentro de WSL2)

Instalar una distro Linux en WSL2 si no existe (`wsl --install`) y, dentro de esa
terminal Linux:

```bash
curl -sfL https://get.k3s.io | \
  K3S_URL=https://<IP_DEL_NODO_LINUX>:6443 \
  K3S_TOKEN=<TOKEN_COPIADO_EN_2.1> \
  sh -s - --node-ip=<IP_PROPIA> --flannel-iface=<INTERFAZ>
```

En LAN, abrir en el firewall de Windows los puertos usados por k3s/flannel si
no conectan: **6443/tcp** (API server), **8472/udp** (VXLAN de flannel) y
**30080/tcp** (NodePort del API Gateway). Con Tailscale estos puertos ya
quedan accesibles entre los peers de la tailnet sin tocar el firewall de
Windows (sí puede hacer falta abrirlos en el firewall **de Linux** si tiene uno
activo, ej. `ufw allow 8472/udp`, `ufw allow 6443/tcp`).

### 2.3 Verificar el clúster

Desde el nodo Linux (o copiando `/etc/rancher/k3s/k3s.yaml` — reemplazando
`127.0.0.1` por la IP del nodo servidor — a `~/.kube/config` en la máquina
desde la que se administre):

```bash
sudo kubectl get nodes -o wide
```

Debe listar **2 nodos** en estado `Ready` (uno Linux, uno Windows/WSL2).

> **Nota:** cada `k3s-uninstall.sh` / `k3s-agent-uninstall.sh` borra por
> completo el estado del clúster (namespace, deployments, certificados) y
> genera un token nuevo — después de reinstalar hay que volver a aplicar todos
> los manifiestos (paso 4) y usar el token/kubeconfig actualizados.

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

## Limitaciones conocidas

- **`kubectl` remoto con certificado de cliente sobre Tailscale**: en nuestra
  configuración, administrar el clúster con `kubectl` desde la máquina que
  *no* es el servidor (autenticando con el certificado de cliente del
  kubeconfig) resultó menos confiable que ejecutar `kubectl`/`k3s kubectl`
  **localmente en el nodo servidor** (donde no hay red de por medio). Para
  evitar depender de esa conexión remota, en la práctica administramos el
  clúster (`apply`, `get pods`, etc.) desde el nodo Linux directamente.

- **`postgres` corre con una sola réplica**; su volumen (`local-path` de k3s)
  queda fijado al nodo donde se programó el pod la primera vez, por lo que la
  caída de ese nodo específico deja la base de datos no disponible hasta que
  vuelva. Es un punto de partida documentado para las siguientes partes de la
  práctica (mecanismos de resiliencia).

## Parte IV — Resultados de las demos en vivo

Las 4 demos se corrieron en vivo sobre el clúster real de 2 nodos, generando
tráfico real con [`scripts/chaos/load_client.py`](scripts/chaos/load_client.py)
en paralelo a cada script de inyección (`scripts/chaos/*.sh`), con logs en vivo
(`kubectl logs -f`) capturados de ambos lados. Evidencia completa (capturas) en
`evidence/`.

| Demo | Patrón | Resultado |
|---|---|---|
| Inventario Fantasma | Retry con backoff | Pod muerto y repuesto en ~10s; sin impacto visible en el cliente (0 errores durante la corrida) |
| Pasarela Lenta | Circuit Breaker | Ciclo completo `closed → open → half-open → open` documentado en logs, con caída de latencia de ~3.3s a ~0.2s tras la apertura |
| Correo Perdido | Fallback | 12/12 reservas confirmadas con `notified:false` durante la caída total de `notificaciones-service`; cero impacto en la venta |
| Base de Datos Intermitente | Retry con backoff | Decenas de reintentos activos en ambos servicios; algunas fallas controladas (502/503/500, nunca cuelgues) cuando el corte (5s) superó el presupuesto acumulado de reintentos (~4.5s) — resultado esperado y documentado, no un fallo del mecanismo |
