# AION Sentinel AI Setup

Do not place `OPENAI_API_KEY` in repo files.

Use a systemd drop-in for the Sentinel service instead.

Suggested setup:

```bash
sudo systemctl edit aion-sentinel.service
```

Add:

```ini
[Service]
Environment=AION_SENTINEL_AI_ENABLED=1
Environment=AION_SENTINEL_AI_SIMULATION=1
Environment=AION_SENTINEL_REQUIRE_HUMAN_CONFIRM=1
Environment=OPENAI_API_KEY=replace_me
```

Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart aion-sentinel.service
```

Keep `AION_SENTINEL_AI_ENABLED=0` until you explicitly want the bridge active.
Simulation mode remains safe and does not apply patches.
