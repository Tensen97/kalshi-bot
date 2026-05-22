AION / KALSHI BOT OPERATING RULES

Canonical System

The live VPS deployment at:

/opt/kalshi-bot

is the canonical production system.

This repo is connected to:
Tensen97/kalshi-bot

All work must:

* modify the live VPS repo directly
* validate locally on VPS
* restart services if needed
* verify logs after changes
* commit and push to GitHub

Never work in detached copies or shadow repos.

⸻

Primary Objective

The primary objective is:

PROFITABLE LIVE TRADE EXECUTION.

The bot exists to:

* participate in markets
* discover profitable behavior
* adapt from live results
* generate income consistently

Architecture, dashboards, telemetry systems, abstractions, and theoretical perfection are secondary.

If the bot is not trading, treat that as the highest-priority system failure.

⸻

Core Philosophy

Prefer:

* real execution
* candidate throughput
* adaptive behavior
* profitable participation
* fast iteration
* execution clarity
* simplification
* practical fixes

Avoid:

* safety theater
* endless filtering
* architecture creep
* telemetry obsession
* hidden blockers
* dead pipelines
* over-engineering
* passive “perfect setup” behavior

The system should behave like an adaptive income machine, not a fragile research project.

⸻

Safety Rules

Preserve ONLY catastrophic protections:

* no duplicate live positions
* sane max sizing
* valid API/orderbook checks
* daily loss cap
* crash prevention

Do NOT add excessive restrictions unless catastrophic risk exists.

Do NOT reject trades simply because:

* liquidity is imperfect
* spread is imperfect
* market is noisy
* setup is not ideal
* price is near boundaries

Controlled aggression is preferred over excessive passivity.

⸻

Execution Rules

The desired live pipeline is:

SCAN
→ QUALIFY
→ EXECUTE
→ MANAGE
→ LEARN

NOT:

SCAN
→ FILTER
→ FILTER
→ FILTER
→ NOTHING

Every approved candidate should visibly progress through:

[AION-QUALIFIED]
→
[AION-DISPATCH]
→
[LIVE-EXECUTION]
→
[ORDER-SUBMIT]
→
[POSITION-OPEN]

Every rejected candidate MUST terminate with exactly one explicit rejection reason:

[AION-FINAL-REJECT]

No silent suppression.
No hidden returns.
No invisible blockers.

Every rejection log should include:

* ticker
* family
* side
* selected_price
* spread
* liquidity
* volume
* rejection reason
* responsible subsystem

⸻

Candidate & Market Philosophy

Prefer:

* broader market participation
* candidate diversity
* multi-family participation
* adaptive thresholds
* profitable live learning

Avoid:

* hyper-concentrating on one family forever
* overfitting to one market type
* dead candidate queues
* waiting for “perfect” conditions

The bot should actively discover where edge exists through live participation.

⸻

Legacy System Rules

When replacing outdated systems:

* preserve catastrophic safety
* verify replacement works
* migrate telemetry if useful
* remove obsolete active logic afterward

Do NOT endlessly patch obsolete restrictive systems.

Prefer canonical replacements over stacked legacy layers.

Avoid duplicate blockers and overlapping gate systems.

⸻

Telemetry Philosophy

Telemetry exists to SUPPORT execution, not replace it.

Logs should:

* explain decisions
* expose failures clearly
* help debugging
* help iteration speed

Logs should NOT:

* become the trading engine
* suppress execution silently
* create defensive paralysis
* spam meaningless noise

High-signal logs are preferred over noisy logs.

⸻

Code Modification Rules

Before finalizing changes:

1. validate syntax
2. run smoke checks if available
3. restart affected services if needed
4. verify live logs
5. confirm execution flow still works
6. confirm no silent candidate death was introduced

Always verify live behavior after modifying execution logic.

⸻

Roadmap Continuity

Preserve roadmap continuity and existing system direction.

Do not randomly rename or reorganize major systems without reason.

Simplify execution logic when beneficial, but avoid chaotic structural rewrites unless explicitly requested.

⸻

Practical Engineering Priority

Priority order:

1. profitable live execution
2. stable order submission
3. execution clarity
4. adaptive improvement
5. debugging speed
6. telemetry quality
7. infrastructure

The bot making real profitable trades matters more than theoretical architecture quality.
