"""Small orderbook helpers moved out of kalshi_web.py."""


def calculate_topbook_imbalance(yes_top_size, no_top_size):
    total_size = max(0.0, yes_top_size) + max(0.0, no_top_size)
    imbalance = ((yes_top_size - no_top_size) / total_size) if total_size > 0.0 else 0.0
    pressure_side = "yes" if imbalance > 0.0 else "no" if imbalance < 0.0 else None
    return {
        "total_size": total_size,
        "imbalance": imbalance,
        "pressure_side": pressure_side,
    }
