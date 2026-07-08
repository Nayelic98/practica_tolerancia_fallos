import logging
import os
import random
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pagos-service")

app = FastAPI(title="Servicio de Pagos (stub)")

# Parámetros configurables vía variables de entorno para poder ajustar
# el "caos" del stub sin reconstruir la imagen.
MIN_LATENCY_MS = int(os.getenv("MIN_LATENCY_MS", "100"))
MAX_LATENCY_MS = int(os.getenv("MAX_LATENCY_MS", "2500"))
FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.2"))  # cobro rechazado
TIMEOUT_RATE = float(os.getenv("TIMEOUT_RATE", "0.05"))  # pasarela colgada
SLOW_TIMEOUT_MS = int(os.getenv("SLOW_TIMEOUT_MS", "8000"))


class ChargeRequest(BaseModel):
    amount: float
    user_email: str


@app.get("/health")
def health():
    return {"status": "ok", "service": "pagos"}


@app.post("/charge")
def charge(req: ChargeRequest):
    roll = random.random()

    if roll < TIMEOUT_RATE:
        logger.warning("simulating a slow/stuck payment gateway")
        time.sleep(SLOW_TIMEOUT_MS / 1000)
        raise HTTPException(status_code=504, detail="payment gateway timeout")

    latency_ms = random.randint(MIN_LATENCY_MS, MAX_LATENCY_MS)
    time.sleep(latency_ms / 1000)

    if roll < TIMEOUT_RATE + FAILURE_RATE:
        logger.warning("payment declined for %s", req.user_email)
        raise HTTPException(status_code=402, detail="payment declined")

    tx_id = str(uuid.uuid4())
    logger.info("payment approved for %s, tx=%s, latency_ms=%s", req.user_email, tx_id, latency_ms)
    return {"transaction_id": tx_id, "amount": req.amount, "latency_ms": latency_ms}
