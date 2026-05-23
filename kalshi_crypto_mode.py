"""Small crypto mode helpers moved out of kalshi_web.py."""


def is_btc_volatility_family(family_key=None, ticker_key=None):
    combined = f"{str(family_key or '').strip().upper()} {str(ticker_key or '').strip().upper()}"
    return bool(
        "KXBTC" in combined
        or "BTC" in combined
        or "CRYPTO" in combined
        or "ETH" in combined
    )
