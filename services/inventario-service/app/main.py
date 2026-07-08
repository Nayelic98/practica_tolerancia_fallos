import logging
import os
from contextlib import contextmanager

import psycopg2
from fastapi import FastAPI, HTTPException
from psycopg2 import pool as pg_pool
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inventario-service")

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "ticketing")
DB_USER = os.getenv("DB_USER", "ticketing")
DB_PASSWORD = os.getenv("DB_PASSWORD", "ticketing")

app = FastAPI(title="Servicio de Inventario")

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
    """Conexión del pool. Ante un corte de conectividad (psycopg2.OperationalError)
    la conexión se descarta (close=True) en vez de devolverse al pool, para que
    el próximo intento abra una conexión nueva en lugar de reutilizar una rota."""
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


class QuantityRequest(BaseModel):
    quantity: int


@app.get("/health")
def health():
    return {"status": "ok", "service": "inventario"}


@db_retry
def _fetch_inventory(event_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT event_id, event_name, total_seats, available_seats "
                "FROM inventory WHERE event_id = %s",
                (event_id,),
            )
            return cur.fetchone()


@app.get("/inventory/{event_id}")
def get_inventory(event_id: str):
    row = _fetch_inventory(event_id)
    if not row:
        raise HTTPException(status_code=404, detail="event not found")
    return {
        "event_id": row[0],
        "event_name": row[1],
        "total_seats": row[2],
        "available_seats": row[3],
    }


@db_retry
def _reserve_seats(event_id, quantity):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE inventory SET available_seats = available_seats - %s "
                "WHERE event_id = %s AND available_seats >= %s "
                "RETURNING available_seats",
                (quantity, event_id, quantity),
            )
            row = cur.fetchone()
            exists = True
            if not row:
                cur.execute("SELECT 1 FROM inventory WHERE event_id = %s", (event_id,))
                exists = cur.fetchone() is not None
            conn.commit()
    return row, exists


@app.post("/inventory/{event_id}/reserve")
def reserve(event_id: str, body: QuantityRequest):
    if body.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")
    row, exists = _reserve_seats(event_id, body.quantity)
    if not row:
        if not exists:
            raise HTTPException(status_code=404, detail="event not found")
        raise HTTPException(status_code=409, detail="not enough seats available")
    logger.info("reserved %s seats for %s, remaining=%s", body.quantity, event_id, row[0])
    return {"event_id": event_id, "reserved": body.quantity, "available_seats": row[0]}


@db_retry
def _release_seats(event_id, quantity):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE inventory SET available_seats = available_seats + %s "
                "WHERE event_id = %s RETURNING available_seats",
                (quantity, event_id),
            )
            row = cur.fetchone()
            conn.commit()
    return row


@app.post("/inventory/{event_id}/release")
def release(event_id: str, body: QuantityRequest):
    row = _release_seats(event_id, body.quantity)
    if not row:
        raise HTTPException(status_code=404, detail="event not found")
    logger.info("released %s seats for %s, available=%s", body.quantity, event_id, row[0])
    return {"event_id": event_id, "released": body.quantity, "available_seats": row[0]}
