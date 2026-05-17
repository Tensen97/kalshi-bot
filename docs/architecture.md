# Architecture Notes

## Modularization Boundary

The orchestrator stays in `kalshi_web.py`.

The first safe extractions are:

- sports calibration helpers
- sports harvest / watchlist helpers
- risk helper functions
- formatting helpers
- persistent memory helpers

These helpers may be imported by the orchestrator, but they do not bypass the live execution path.

## Migration Rule

Move isolated pure helpers first.

Keep gate logic, order placement, and live approval logic where it is until a dedicated refactor is safe.

