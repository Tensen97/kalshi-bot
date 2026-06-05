"""Modular runtime services for the Kalshi sniper bot.

The legacy entry point remains ``kalshi_web.py``.  These modules isolate the
latency-sensitive pieces so the large compatibility file can delegate to
testable services without changing its public CLI.
"""

from .core import BackoffConfig, CircuitBreaker, KalshiHttpClient, RateLimiter
from .discovery import MarketDiscoveryConfig, MarketDiscoveryService
from .risk import EntryExitGuardService, GuardThresholds

__all__ = [
    "BackoffConfig",
    "CircuitBreaker",
    "EntryExitGuardService",
    "GuardThresholds",
    "KalshiHttpClient",
    "MarketDiscoveryConfig",
    "MarketDiscoveryService",
    "RateLimiter",
]
