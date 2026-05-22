#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path('/opt/kalshi-bot/kalshi_web.py')


def load_module():
    spec = importlib.util.spec_from_file_location('kalshi_web', ROOT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def make_bot(module):
    return object.__new__(module.KalshiSniperBot)


def run_case(name, func):
    try:
        value = func()
        return {'case': name, 'ok': True, 'value': value}
    except Exception as exc:
        return {'case': name, 'ok': False, 'error_type': exc.__class__.__name__, 'error_message': str(exc)}


def main():
    module = load_module()
    bot = make_bot(module)
    cases = [
        ('missing_quote', lambda: bot.build_quote_context(ticker='T1', trace_id='x', family='F')),
        ('partial_selected_price', lambda: bot.build_quote_context(ticker='T2', trace_id='x', family='F', selected_price=0.42)),
        ('valid_two_sided', lambda: bot.build_quote_context(ticker='T3', trace_id='x', family='F', selected_side='yes', selected_price=0.42, selected_price_cents=42, yes_bid=0.41, yes_ask=0.42, no_bid=0.58, no_ask=0.59, spread_cents=1, executable_pair_present=True, executable_depth_present=True, passed=True, reason='ok')),
        ('complementary_book', lambda: bot.build_spread_context(ticker='T4', trace_id='x', family='F', selected_side='no', selected_price=0.55, no_bid=0.54, no_ask=0.55, yes_bid=0.45, yes_ask=0.46, quote_source='complementary', orderbook_source='market', passed=True, reason='computed')),
        ('fake_0_1_book', lambda: bot.build_quote_context(ticker='T5', trace_id='x', family='F', selected_side='no', selected_price=1.0, selected_price_cents=100, yes_bid=0.0, yes_ask=0.0, no_bid=1.0, no_ask=1.0, spread_cents=0, executable_pair_present=False, executable_depth_present=True, passed=False, reason='selected_price_above_0_99')),
        ('missing_spread', lambda: bot.build_spread_context(ticker='T6', trace_id='x', family='F', selected_side='yes', selected_price=0.51, yes_ask=0.51, yes_bid=0.50, no_bid=None, no_ask=None, spread_cents=None, executable_pair_present=True, executable_depth_present=True, passed=True, reason='computed')),
        ('malformed_payload', lambda: bot.build_submit_payload(ticker='T7', trace_id='x', family='F', selected_side=None, selected_price=None, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None, spread_cents=None, spread_present=False, executable_pair_present=False, executable_depth_present=False, quote_source='payload', orderbook_source='payload', passed=False, reason='quote_invalid')),
        ('missing_telemetry_fields', lambda: bot.build_quote_context(ticker='T8', trace_id=None, family=None)),
        ('missing_canonical_quote_payload', lambda: bot.build_submit_payload(ticker='T9', trace_id='x', family='F', selected_side='yes', selected_price=0.33, yes_bid=0.32, yes_ask=0.33, no_bid=0.67, no_ask=0.68, quote_source='canonical', orderbook_source='market', passed=True, reason='ok')),
        ('missing_quote_content_pass_value', lambda: bot.build_quote_context(ticker='T10', trace_id='x', family='F', selected_side='yes', selected_price=0.44)),
        ('missing_source_fields_checked', lambda: bot.build_submit_payload(ticker='T11', trace_id='x', family='F', selected_side='yes', selected_price=0.44, yes_bid=0.43, yes_ask=0.44)),
    ]
    results = [run_case(name, func) for name, func in cases]
    failed = [r for r in results if not r['ok']]
    print(json.dumps({'results': results, 'passed': len(results) - len(failed), 'failed': len(failed)}, sort_keys=True))
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
