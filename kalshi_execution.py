"""Small execution helpers moved out of kalshi_web.py."""

import hashlib
import os
import time


def ensure_live_client_order_id(payload):
    payload = dict(payload or {})
    if not payload.get("client_order_id"):
        seed = f"{payload.get('ticker')}:{time.time_ns()}:{os.getpid()}"
        payload["client_order_id"] = "aion-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return payload
