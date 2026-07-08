import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("notificaciones-service")

app = FastAPI(title="Servicio de Notificaciones (stub)")

MIN_LATENCY_MS = int(os.getenv("MIN_LATENCY_MS", "50"))
MAX_LATENCY_MS = int(os.getenv("MAX_LATENCY_MS", "1500"))
FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.15"))


class NotifyRequest(BaseModel):
    email: str
    message: str


@app.get("/health")
def health():
    return {"status": "ok", "service": "notificaciones"}


@app.post("/notify")
def notify(req: NotifyRequest):
    latency_ms = random.randint(MIN_LATENCY_MS, MAX_LATENCY_MS)
    time.sleep(latency_ms / 1000)

    if random.random() < FAILURE_RATE:
        logger.warning("failed to send notification to %s", req.email)
        raise HTTPException(status_code=503, detail="notification provider unavailable")

    logger.info("notification sent to %s: %s", req.email, req.message)
    return {"status": "sent", "email": req.email, "latency_ms": latency_ms}
