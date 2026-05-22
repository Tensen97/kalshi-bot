#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path('/opt/kalshi-bot/kalshi_web.py')
TEXT = ROOT.read_text()
PATTERNS = [
    'canonical_quote_payload',
    'canonical_quote_payload_result',
    'quote_content_pass_value',
    'source_fields_checked',
    'selected_quote',
    'quote_object',
    'payload[',
]

lines = TEXT.splitlines()
findings = []
for idx, line in enumerate(lines, start=1):
    for pat in PATTERNS:
        if pat in line:
            findings.append({'line': idx, 'pattern': pat, 'text': line.strip()[:220]})
            break

print({'file': str(ROOT), 'findings': findings[:200], 'finding_count': len(findings)})
