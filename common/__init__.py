"""Shared utilities for TG Crawler (normalize, extracted parsing, etc.).

Used by both crawler and web to eliminate duplication.
Import as: from common.normalize import normalize_code
"""

from .normalize import normalize_code, normalize_code_key
from .extracted import (
    EXTRACTED_PROFILE_KEYS,
    has_meaningful_extracted,
    parse_int,
    parse_float,
    parse_bool,
    to_int,  # alias for backward compat
    to_float,
    to_bool,
    is_empty_value,
    parse_extracted_value,
    merge_extracted,
    extracted_score,
)

__all__ = [
    "normalize_code",
    "normalize_code_key",
    "EXTRACTED_PROFILE_KEYS",
    "has_meaningful_extracted",
    "to_int",
    "to_float",
    "to_bool",
    "is_empty_value",
    "parse_extracted_value",
    "merge_extracted",
    "extracted_score",
]