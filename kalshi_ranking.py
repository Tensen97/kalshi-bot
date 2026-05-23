"""Small ranking helpers moved out of kalshi_web.py."""


def ev_rank_adjustment(ev_context, to_float):
    ev_context = ev_context if isinstance(ev_context, dict) else {}
    ev_score = to_float(ev_context.get("ev_score"), 0.0) or 0.0
    ev_tier = str(ev_context.get("ev_tier") or "").strip().upper()
    if ev_tier == "S":
        return max(1.50, min(4.00, ev_score * 0.35))
    if ev_tier == "A":
        return max(0.75, min(2.50, ev_score * 0.30))
    if ev_tier == "B":
        return max(0.10, min(1.25, ev_score * 0.20))
    if ev_tier == "C":
        return max(-0.25, min(0.25, ev_score * 0.10))
    if ev_tier == "D":
        return min(-0.50, max(-3.50, ev_score * 0.25))
    return max(-0.25, min(0.25, ev_score * 0.10))
