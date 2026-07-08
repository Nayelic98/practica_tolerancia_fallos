import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api-gateway")

RESERVAS_URL = os.getenv("RESERVAS_URL", "http://reservas-service:8000")
INVENTARIO_URL = os.getenv("INVENTARIO_URL", "http://inventario-service:8000")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "6"))

app = FastAPI(title="API Gateway")


class ReservationRequest(BaseModel):
    event_id: str
    user_email: str
    quantity: int


@app.get("/health")
def health():
    return {"status": "ok", "service": "api-gateway"}


@app.get("/api/events/{event_id}")
def get_event(event_id: str):
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(f"{INVENTARIO_URL}/inventory/{event_id}")
    except httpx.RequestError:
        logger.error("inventario unreachable")
        raise HTTPException(status_code=503, detail="inventory service unavailable")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="event not found")
    return resp.json()


@app.post("/api/reservations")
def create_reservation(req: ReservationRequest):
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(f"{RESERVAS_URL}/reservations", json=req.model_dump())
    except httpx.RequestError:
        logger.error("reservas unreachable")
        raise HTTPException(status_code=503, detail="reservations service unavailable")
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "error"))
    return resp.json()


@app.get("/api/reservations/{reservation_id}")
def get_reservation(reservation_id: int):
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(f"{RESERVAS_URL}/reservations/{reservation_id}")
    except httpx.RequestError:
        logger.error("reservas unreachable")
        raise HTTPException(status_code=503, detail="reservations service unavailable")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="reservation not found")
    return resp.json()
