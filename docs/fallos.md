# Parte II — Catálogo de fallos y mecanismo de inyección

| # | Fallo | Categoría | Mecanismo técnico de inyección sobre el clúster de 2 nodos |
|---|---|---|---|
| 1 | El Inventario Fantasma | Disponibilidad | `kubectl delete pod` sobre una de las 2 réplicas de `inventario-service` (`--grace-period=0 --force` para simular un crash, no un desalojo ordenado) mientras hay reservas en curso |
| 2 | La Pasarela Lenta | Latencia | El propio stub de `pagos-service` ya simula latencia/timeouts configurables; se fuerza el 100% de las peticiones a tardar ~20s vía `kubectl set env deployment/pagos-service TIMEOUT_RATE=1 SLOW_TIMEOUT_MS=20000` |
| 3 | El Diluvio de Peticiones | Sobrecarga | Generador de carga externo (k6/JMeter, o el `load_client.py` de este repo a mayor frecuencia) apuntando al NodePort `:30080` del API Gateway |
| 4 | Base de Datos Intermitente | Conectividad | k3s usa Flannel por defecto, que **no aplica NetworkPolicies**, así que se optó por un sidecar **Toxiproxy** delante de Postgres que corta/restablece la conexión (`enabled: false/true`) en ciclos vía su API HTTP |
| 5 | El Correo Perdido | Fallo no crítico | `kubectl scale deployment/notificaciones-service --replicas=0` |
| 6 | Condición de Carrera | Consistencia | Múltiples clientes concurrentes (hilos/asyncio o k6 con VUs simultáneos) contra `POST /api/reservations` sobre un evento con 1 solo asiento disponible |

## Fallos implementados en la Parte III

De los 6, se eligieron **4** para implementar mecanismo de defensa + inyección real +
evidencia (ver [resilience.md](resilience.md)):

1. El Inventario Fantasma → **Retry con backoff**
2. La Pasarela Lenta → **Circuit Breaker**
3. El Correo Perdido → **Fallback**
4. Base de Datos Intermitente → **Retry con backoff** (a nivel de conexión a Postgres)

Quedan fuera de esta entrega (documentados pero sin mecanismo implementado):
**El Diluvio de Peticiones** (requeriría Bulkhead/rate-limiting + `metrics-server`/HPA,
descartado por el riesgo de inestabilidad en un clúster mixto Linux + WSL2) y
**Condición de Carrera** (ya resuelta de raíz en la Parte I mediante el `UPDATE ...
WHERE available_seats >= qty` atómico de `inventario-service`, sin patrón nuevo que
justifique ocupar uno de los 4 cupos).
