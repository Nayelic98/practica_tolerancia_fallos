# Parte III — Mecanismos de resiliencia implementados

Los 4 fallos elegidos (ver [fallos.md](fallos.md)) y su patrón:

| Fallo | Patrón | Por qué ese y no otro |
|---|---|---|
| Inventario Fantasma | **Retry con backoff** (solo ante `httpx.ConnectError`) | El problema es la caída de *una* réplica, no una sobrecarga sostenida — no hace falta un Circuit Breaker. Un Bulkhead tampoco aplica: no hay recursos compartidos que aislar. Reintentar aprovecha directamente que la Service de k8s ya balancea contra la réplica del otro nodo. Se limita a errores de conexión (nunca a timeouts de lectura) porque la operación decrementa cupo y **no es idempotente**: reintentar una petición que sí llegó a aplicarse duplicaría el descuento. |
| La Pasarela Lenta | **Circuit Breaker** | Un retry aquí sería contraproducente: si la pasarela está degradada, reintentar multiplica la carga sobre un servicio ya lento y cada intento vuelve a pagar el costo completo del timeout. El Circuit Breaker deja de llamar a Pagos apenas detecta fallos sostenidos, respondiendo rápido (fail-fast) mientras dura la degradación, y prueba sola la recuperación (half-open) pasado un tiempo. |
| El Correo Perdido | **Fallback** | Es, por definición, un fallo no crítico: la reserva ya está pagada y confirmada. Ni Circuit Breaker ni Bulkhead agregan valor aquí — lo único que importa es no revertir una compra válida por un problema en un servicio secundario. Se agregó un reintento corto (2 intentos) por si el fallo es transitorio, y si persiste, se degrada explícitamente (`notified: false`) sin bloquear al usuario. |
| Base de Datos Intermitente | **Retry con backoff** + descarte de conexiones rotas del pool | El "flapping" es, por definición, transitorio (la conexión vuelve sola en segundos). Un Circuit Breaker no tiene sentido para la propia base de datos del servicio: si abre, el servicio queda inutilizable por completo. El backoff exponencial evita bombardear una base ya inestable con reconexiones inmediatas, y descartar del pool la conexión que falló evita que el siguiente intento reutilice una conexión ya muerta. |

## 1. Inventario Fantasma — Retry con backoff

**Código:** [`services/reservas-service/app/main.py`](../services/reservas-service/app/main.py)
— función `_post_inventario` (decorada con `tenacity.retry`, `stop_after_attempt(3)`,
`wait_exponential`, `retry_if_exception_type(httpx.ConnectError)`).

**Inyección:** [`scripts/chaos/kill-inventario-pod.sh`](../scripts/chaos/kill-inventario-pod.sh)
borra una de las 2 réplicas mientras corre `load_client.py` en paralelo.

**Evidencia a capturar:**
```bash
kubectl -n ticketing get pods -l app=inventario-service -o wide   # antes y después
kubectl -n ticketing logs -l app=reservas-service --since=2m | grep -i "reintento\|inventario"
```
Se espera ver 1-2 líneas `reintento N llamando a inventario-service` en el/los request(s)
que coincidieron con la ventana de caída del pod, y que **ninguna** petición del
`load_client.py` haya devuelto `503` de forma sostenida (a lo sumo alguna aislada
mientras el scheduler reprograma el pod).

## 2. La Pasarela Lenta — Circuit Breaker

**Código:** mismo archivo — `pagos_breaker` (`pybreaker.CircuitBreaker`, `fail_max=3`,
`reset_timeout=30`, `exclude=[PaymentDeclined]`) envolviendo `_charge()`, con
`_BreakerLogger` para dejar constancia de cada transición de estado.

**Inyección:** [`scripts/chaos/slow-payment-gateway.sh`](../scripts/chaos/slow-payment-gateway.sh).

**Evidencia a capturar:**
```bash
kubectl -n ticketing logs -l app=reservas-service -f | grep -i breaker
```
Se espera la secuencia `closed -> open` a los ~3 fallos/timeouts consecutivos, y que
las peticiones posteriores (mientras el breaker está abierto) respondan **mucho más
rápido** que `PAGOS_TIMEOUT` (no llegan a llamar a la pasarela), y finalmente
`open -> half-open -> closed` cuando `slow-payment-gateway.sh` restaura el
comportamiento normal. Comparar la columna `latencia=` del `load_client.py` antes,
durante y después.

**Limitación conocida:** el estado del breaker es local a cada proceso; con 2 réplicas
de `reservas-service` cada una lleva su propio conteo de fallos (no hay un estado
compartido tipo Redis). Para esta práctica es aceptable — en producción se
compartiría el estado entre réplicas.

## 3. El Correo Perdido — Fallback

**Código:** mismo archivo — `_post_notify()` (retry corto de 2 intentos) y el bloque
`try/except` en `create_reservation()` que marca `notified=False` sin lanzar excepción
ni revertir la reserva ya persistida como `CONFIRMED`.

**Inyección:** [`scripts/chaos/kill-notificaciones.sh`](../scripts/chaos/kill-notificaciones.sh).

**Evidencia a capturar:** una petición `POST /api/reservations` exitosa (`200`,
`status: CONFIRMED`) con `notified: false` mientras `notificaciones-service` está en
0 réplicas, más la línea de log `fallback: no se pudo notificar a ... la reserva N
queda CONFIRMED con notified=False`.

## 4. Base de Datos Intermitente — Retry con backoff

**Código:** [`services/inventario-service/app/main.py`](../services/inventario-service/app/main.py)
y [`services/reservas-service/app/main.py`](../services/reservas-service/app/main.py)
— decorador `db_retry` (`stop_after_attempt(4)`, `wait_exponential`,
`retry_if_exception_type(psycopg2.OperationalError)`) sobre todas las funciones que
tocan Postgres, y `get_conn()` descartando (`close=True`) la conexión cuando falla.

**Inyección:** [`scripts/chaos/flap-database.sh`](../scripts/chaos/flap-database.sh)
(Toxiproxy) + [`restore-database.sh`](../scripts/chaos/restore-database.sh) para volver
al estado normal.

**Evidencia a capturar:**
```bash
kubectl -n ticketing logs -l app=inventario-service --since=3m | grep -i "reintento"
kubectl -n ticketing logs -l app=reservas-service --since=3m | grep -i "reintento.*postgres"
```
Se espera ver reintentos concentrados en las ventanas de "caído" del flapping, y que
el `load_client.py` corriendo en paralelo muestre, a lo sumo, algunos `503`/errores
aislados durante los cortes más largos, recuperándose solo sin reiniciar los pods.

## Cómo dejar constancia (Paso 4 de la consigna)

Cada integrante que corra una demo debe guardar, en una carpeta `evidence/<nombre>/`
(no versionada por defecto — ver `.gitignore`, remover esa línea si van a subir la
evidencia al repo):
- La salida de `kubectl get pods -o wide` antes/después de cada inyección.
- Un `kubectl logs ... > evidence/.../<escenario>.log` de la ventana del incidente.
- La salida de `load_client.py` (o capturas de pantalla) mostrando que las peticiones
  siguieron devolviendo `200`/errores controlados, nunca timeouts colgados ni el
  API Gateway caído.

Esta parte requiere el clúster real de 2 nodos corriendo — no se puede generar desde
este entorno de desarrollo, que no tiene acceso a las dos computadoras físicas.
