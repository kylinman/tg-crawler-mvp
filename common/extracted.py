"""Shared logic for handling extracted_json payloads and profile fields.

Extracted from crawler/db.py and related code to avoid duplication
with web layer.
"""

import json
from typing import Any, Dict, Optional, Set

# Fields that, when present with a non-empty value, mean we have
# meaningful person information worth upserting into profiles.
EXTRACTED_PROFILE_KEYS: Set[str] = {
    'nickname',
    'code',
    'province',
    'city',
    'age',
    'height',
    'weight',
    'cup',
    'occupation',
    'is_virgin',
    'oral',
    'creampie',
    'condomless',
    'sm',
    'tattoo',
    'out_province',
    'overnight',
    'cohabitation',
    'monthly_allowance',
    'intro_fee',
    'contacts',
    'tags',
}


def is_empty_value(value: Any) -> bool:
    """Return True if value is semantically empty for our purposes."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def has_meaningful_extracted(extracted: Any) -> bool:
    """Returns True when extracted payload has at least one person field value.

    Used to decide whether to create/update a profile row.
    """
    if not isinstance(extracted, dict) or not extracted:
        return False
    for key in EXTRACTED_PROFILE_KEYS:
        if key not in extracted:
            continue
        if not is_empty_value(extracted.get(key)):
            return True
    return False


def parse_int(value: Any) -> Optional[int]:
    """Parse to int, handling None, str (with strip), and other types.
    Compatible with both extracted payloads and form inputs.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> Optional[float]:
    """Parse to float, handling None, str (with strip), etc."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bool(value: Any) -> Optional[bool]:
    """Parse to bool from various inputs (str, int, etc.)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        value = value.strip().lower()
        if not value:
            return None
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'y', 'on', 'ok'}:
        return True
    if text in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return None


# Backwards-compatible aliases (used internally in crawler/db)
to_int = parse_int
to_float = parse_float
to_bool = parse_bool


def parse_extracted_value(extracted_value: Any) -> Dict[str, Any]:
    """Normalizes extracted_json value (str or dict) to a clean dict."""
    if isinstance(extracted_value, dict):
        return extracted_value
    if isinstance(extracted_value, str):
        try:
            loaded = json.loads(extracted_value)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            return {}
    return {}


def merge_extracted(base: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Fills missing person fields from fallback extracted payload.

    Preserves base values when present and non-empty.
    Special handling for internal _found_fields, confidence, _status.
    """
    merged = dict(base or {})
    for key, value in (fallback or {}).items():
        if key.startswith('_'):
            continue
        if key not in merged or is_empty_value(merged.get(key)):
            merged[key] = value

    if '_found_fields' in fallback:
        merged['_found_fields'] = max(
            int(base.get('_found_fields') or 0),
            int(fallback.get('_found_fields') or 0),
        )
    if 'confidence' in fallback:
        merged['confidence'] = max(
            float(base.get('confidence') or 0),
            float(fallback.get('confidence') or 0),
        )
    if '_status' in fallback and (base.get('_status') in (None, '', 'failed')):
        merged['_status'] = fallback.get('_status')
    return merged


def extracted_score(extracted: Dict[str, Any]) -> int:
    """Heuristic score for choosing the 'best' extracted result in a media group."""
    score = int(extracted.get('_found_fields') or 0)
    if extracted.get('code'):
        score += 100
    if extracted.get('nickname'):
        score += 20
    return score
