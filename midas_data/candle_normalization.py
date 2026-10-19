"""Admit archived candles through the existing reader contract, without token facts.

A separate immutable version preserves all historical gmgn-p0-v4 results.
Only known bar fields receive candle semantics; other fields remain catalogued.
"""
from .contracts import NormalizationResult, Issue
from .candles import parse_candles, CandleContract, VERSION as READER_VERSION

VERSION = 'gmgn-candles-normalize-v1'
BAR_FIELDS = frozenset({'time', 'open', 'high', 'low', 'close', 'volume'})


def normalize_candle_capture(metadata, raw, catalogue):
    from .normalize import FATAL_ISSUE_CODES, _OutputBudget
    result = catalogue.model_copy(update={'normalizer_version': VERSION})
    if any(i.code in FATAL_ISSUE_CODES for i in result.issues):
        return result
    result.issues[:] = [i for i in result.issues if i.code != 'unsupported_endpoint']
    try:
        # Admission validates the same bars as the reader. No historical
        # availability is persisted here; Store assigns a fresh publication time.
        parsed = parse_candles(metadata, raw, metadata.received_at)
    except CandleContract as exc:
        result.issues.append(Issue(code='candle_contract_error', pointer='', detail=str(exc)))
        return result
    rows = {row['pointer']: row for row in parsed['items']}
    budget = _OutputBudget()
    fields = []
    for observation in result.fields:
        parent, _, name = observation.pointer.rpartition('/')
        row = rows.get(parent)
        if row is not None and name in BAR_FIELDS:
            available = row['state'] == 'available'
            observation = observation.model_copy(update={
                'category': 'ohlcv',
                'quality': ('valid' if available else 'missing' if observation.quality == 'missing' else 'invalid'),
                'reason': 'provider_bar_contract_checked' if available else row['reason'],
                'contract_id': READER_VERSION + ':' + name,
            })
        budget.add_field(observation)
        fields.append(observation)
    for row in rows.values():
        if row['state'] != 'available':
            result.issues.append(Issue(code='invalid_candle', pointer=row['pointer'], detail=row['reason']))
    return NormalizationResult(normalizer_version=VERSION, fields=fields, issues=result.issues)
