import logging
import os
from contextlib import contextmanager

import httpx
import psycopg2
import pybreaker
from psycopg2 import pool as pg_pool
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from fastapi import FastAPI, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reservas-service")

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "ticketing")
DB_USER = os.getenv("DB_USER", "ticketing")
DB_PASSWORD = os.getenv("DB_PASSWORD", "ticketing")

INVENTARIO_URL = os.getenv("INVENTARIO_URL", "http://inventario-service:8000")
PAGOS_URL = os.getenv("PAGOS_URL", "http://pagos-service:8000")
NOTIFICACIONES_URL = os.getenv("NOTIFICACIONES_URL", "http://notificaciones-service:8000")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "5"))

# Timeout específico y más corto para Pagos: no tiene sentido copiar el timeout
# general si lo que queremos es detectar rápido una pasarela lenta.
PAGOS_TIMEOUT = float(os.getenv("PAGOS_TIMEOUT", "3"))

app = FastAPI(title="Servicio de Reservas (Core)")

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = pg_pool.SimpleConnectionPool(
            1,
            10,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=3,
        )
    return _pool


@contextmanager
def get_conn():
    """Conexión del pool. Si la operación falla por un corte de conectividad
    (psycopg2.OperationalError), la conexión rota se descarta del pool en vez
    de devolverse para reutilización — de lo contrario, el siguiente request
    reutilizaría una conexión ya muerta y fallaría también."""
    p = get_pool()
    conn = p.getconn()
    broken = False
    try:
        yield conn
    except psycopg2.OperationalError:
        broken = True
        raise
    finally:
        p.putconn(conn, close=broken)


# --- Retry con backoff para la Base de Datos Intermitente (flapping) ---
db_retry = retry(
    stop=stop_after_attempt(int(os.getenv("DB_RETRY_ATTEMPTS", "4"))),
    wait=wait_exponential(multiplier=0.3, max=3),
    retry=retry_if_exception_type(psycopg2.OperationalError),
    reraise=True,
    before_sleep=lambda rs: logger.warning(
        "reintento %s tras corte de conexión a Postgres", rs.attempt_number
    ),
)


@db_retry
def save_reservation(event_id, user_email, quantity, status, payment_id=None, notified=False):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO reservations
                   (event_id, user_email, quantity, status, payment_id, notified, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, now(), now())
                   RETURNING id""",
                (event_id, user_email, quantity, status, payment_id, notified),
            )
            new_id = cur.fetchone()[0]
            conn.commit()
    return new_id


@db_retry
def mark_notified(reservation_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reservations SET notified = TRUE, updated_at = now() WHERE id = %s",
                (reservation_id,),
            )
            conn.commit()


@db_retry
def fetch_reservation(reservation_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, event_id, user_email, quantity, status, payment_id, notified, created_at "
                "FROM reservations WHERE id = %s",
                (reservation_id,),
            )
            return cur.fetchone()


# --- Retry con backoff para el Inventario Fantasma ---
# Solo se reintenta ante httpx.ConnectError: significa que la conexión nunca se
# estableció (el pod murió / no hay quien atienda), por lo que sabemos que la
# petición NUNCA llegó a aplicarse. Si reintentáramos también ante timeouts de
# lectura (ReadTimeout) correríamos el riesgo de reintentar una operación que
# el servidor ya procesó pero cuya respuesta se perdió, descontando el cupo dos
# veces (la operación no es idempotente).
@retry(
    stop=stop_after_attempt(int(os.getenv("INVENTARIO_RETRY_ATTEMPTS", "3"))),
    wait=wait_exponential(multiplier=0.2, max=2),
    retry=retry_if_exception_type(httpx.ConnectError),
    reraise=True,
    before_sleep=lambda rs: logger.warning(
        "reintento %s llamando a inventario-service (réplica no disponible)", rs.attempt_number
    ),
)
def _post_inventario(client, path, json_body):
    return client.post(f"{INVENTARIO_URL}{path}", json=json_body)


# --- Circuit Breaker para la Pasarela Lenta ---
class PaymentDeclined(Exception):
    """Fallo de negocio (tarjeta rechazada): NO debe abrir el circuit breaker."""


class _BreakerLogger(pybreaker.CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state):
        logger.warning(
            "circuit breaker '%s': %s -> %s", cb.name, old_state.name, new_state.name
        )


pagos_breaker = pybreaker.CircuitBreaker(
    fail_max=int(os.getenv("CB_FAIL_MAX", "3")),
    reset_timeout=int(os.getenv("CB_RESET_TIMEOUT", "30")),
    exclude=[PaymentDeclined],
    listeners=[_BreakerLogger()],
    name="pagos-service",
)


def _charge(amount, user_email):
    with httpx.Client(timeout=PAGOS_TIMEOUT) as client:
        resp = client.post(f"{PAGOS_URL}/charge", json={"amount": amount, "user_email": user_email})
    if resp.status_code == 402:
        raise PaymentDeclined(resp.json().get("detail", "payment declined"))
    resp.raise_for_status()
    return resp.json()["transaction_id"]


# --- Retry + Fallback para el Correo Perdido ---
# Un fallo de notificaciones nunca debe revertir una reserva ya cobrada: se
# reintenta un par de veces por si es un error transitorio y, si sigue
# fallando, se degrada (notified=False) sin afectar el resultado de la compra.
@retry(
    stop=stop_after_attempt(int(os.getenv("NOTIF_RETRY_ATTEMPTS", "2"))),
    wait=wait_exponential(multiplier=0.3, max=2),
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    reraise=True,
)
def _post_notify(client, email, message):
    resp = client.post(f"{NOTIFICACIONES_URL}/notify", json={"email": email, "message": message})
    resp.raise_for_status()
    return resp


class ReservationRequest(BaseModel):
    event_id: str
    user_email: str
    quantity: int


@app.get("/health")
def health():
    return {"status": "ok", "service": "reservas"}


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: int):
    row = fetch_reservation(reservation_id)
    if not row:
        raise HTTPException(status_code=404, detail="reservation not found")
    return {
        "id": row[0],
        "event_id": row[1],
        "user_email": row[2],
        "quantity": row[3],
        "status": row[4],
        "payment_id": row[5],
        "notified": row[6],
        "created_at": row[7].isoformat(),
    }


@app.post("/reservations")
def create_reservation(req: ReservationRequest):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")

    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        # 1. Reservar cupo en Inventario (con retry ante caída de réplica)
        try:
            inv_resp = _post_inventario(
                client, f"/inventory/{req.event_id}/reserve", {"quantity": req.quantity}
            )
        except httpx.RequestError as e:
            logger.error("inventario-service no disponible tras reintentos: %s", e)
            raise HTTPException(status_code=503, detail="inventory service unavailable")

        if inv_resp.status_code == 409:
            raise HTTPException(status_code=409, detail="not enough seats available")
        if inv_resp.status_code == 404:
            raise HTTPException(status_code=404, detail="event not found")
        if inv_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="inventory service error")

        # 2. Cobrar el pago protegido por circuit breaker
        payment_id = None
        payment_ok = False
        try:
            payment_id = pagos_breaker.call(_charge, req.quantity * 10.0, req.user_email)
            payment_ok = True
        except PaymentDeclined as e:
            logger.warning("pago rechazado para %s: %s", req.user_email, e)
        except pybreaker.CircuitBreakerError:
            logger.error(
                "circuit breaker de pagos-service ABIERTO: se falla rápido sin llamar a la pasarela"
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("fallo de infraestructura en pagos-service: %s", e)

        if not payment_ok:
            # Compensar: liberar el cupo reservado (patrón saga)
            try:
                _post_inventario(client, f"/inventory/{req.event_id}/release", {"quantity": req.quantity})
            except httpx.RequestError as e:
                logger.error("no se pudo liberar el cupo tras fallo de pago: %s", e)
            reservation_id = save_reservation(req.event_id, req.user_email, req.quantity, "FAILED")
            raise HTTPException(status_code=402, detail=f"payment failed (reservation {reservation_id})")

        # 3. Persistir como confirmada
        reservation_id = save_reservation(
            req.event_id, req.user_email, req.quantity, "CONFIRMED", payment_id=payment_id
        )

        # 4. Notificar (retry + fallback: nunca revierte la reserva ya pagada)
        notified = False
        try:
            _post_notify(
                client, req.user_email, f"Reserva {reservation_id} confirmada para {req.event_id}"
            )
            notified = True
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(
                "fallback: no se pudo notificar a %s tras reintentos (%s); "
                "la reserva %s queda CONFIRMED con notified=False",
                req.user_email, e, reservation_id,
            )

        if notified:
            mark_notified(reservation_id)

    return {
        "reservation_id": reservation_id,
        "event_id": req.event_id,
        "quantity": req.quantity,
        "status": "CONFIRMED",
        "payment_id": payment_id,
        "notified": notified,
    }
