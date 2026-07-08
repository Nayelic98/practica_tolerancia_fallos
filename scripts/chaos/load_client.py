#!/usr/bin/env python3
"""Cliente de carga simple (solo stdlib) para generar tráfico real durante
las demos de inyección de fallos.

Uso:
    python scripts/chaos/load_client.py http://<IP_NODO>:30080 \
        --event evt-002 --interval 0.5 --duration 60
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime


def post_reservation(base_url, event_id, user_email, quantity):
    url = f"{base_url}/api/reservations"
    body = json.dumps(
        {"event_id": event_id, "user_email": user_email, "quantity": quantity}
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            elapsed = time.monotonic() - start
            return resp.status, elapsed, resp.read().decode()
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - start
        return e.code, elapsed, e.read().decode()
    except urllib.error.URLError as e:
        elapsed = time.monotonic() - start
        return None, elapsed, str(e.reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="p.ej. http://192.168.1.10:30080")
    parser.add_argument("--event", default="evt-002")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=60.0)
    args = parser.parse_args()

    end = time.monotonic() + args.duration
    n = 0
    while time.monotonic() < end:
        n += 1
        email = f"cliente{n}@demo.com"
        status, elapsed, body = post_reservation(args.base_url, args.event, email, 1)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] req#{n} status={status} latencia={elapsed:.2f}s body={body[:150]}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
