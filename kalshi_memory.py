"""Small memory helpers moved out of kalshi_web.py."""


def normalize_memory_family_value(family=None, ticker=None, default="UNKNOWN"):
    family_value = str(family or "").strip().upper()
    if family_value in {"NONE", "NULL", "UNKNOWN"}:
        family_value = ""
    if not family_value and ticker:
        family_value = str(ticker or "").split("-")[0].strip().upper()
        if family_value in {"NONE", "NULL", "UNKNOWN"}:
            family_value = ""
    return family_value or default
