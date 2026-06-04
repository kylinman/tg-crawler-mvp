"""Normalization helpers shared across crawler and web.

These ensure consistent handling of 'code' fields (internal numbering)
and search keys.
"""

import re
from typing import Optional


def normalize_code(value: Optional[str]) -> Optional[str]:
    """Normalize a business code (e.g. 'R3435', 'A-123').

    - Strips whitespace and common quote characters.
    - Removes anything not in [A-Za-z0-9_-].
    - Returns None for empty result.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r'[`\s]+', '', text)
    text = re.sub(r'[^A-Za-z0-9_-]', '', text)
    return text or None


def normalize_code_key(value: Optional[str]) -> Optional[str]:
    """Create a canonical lookup key for a code (used in search / grouping).

    Applies normalize_code then:
    - Removes all non-alphanumeric characters.
    - Lowercases the result.
    Useful for case-insensitive, punctuation-insensitive matching
    (e.g. person grouping by code).
    """
    text = normalize_code(value)
    if not text:
        return None
    text = re.sub(r'[^A-Za-z0-9]+', '', text)
    text = text.lower()
    return text or None
