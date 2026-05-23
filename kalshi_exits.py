"""Small exit helpers moved out of kalshi_web.py."""


def coerce_live_exit_price(value, to_float):
    price = to_float(value, None)
    if price is None:
        return None
    if 0.0 < price <= 1.0:
        return round(price, 4)
    if 1.0 < price <= 100.0:
        return round(price / 100.0, 4)
    return None
