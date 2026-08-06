"""
Small normalization helpers shared by every ingest script.

The whole point of this module: data from different sources describes the same
real-world thing in slightly different ways. "St. Louis County", "ST LOUIS",
and "Saint Louis" are one county. "(602) 555-0100" and "602-555-0100" are one
phone number. Normalizing once, here, keeps that mess out of everything else.
"""

import re
import unicodedata

# --------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------

def normalize_phone(value):
    """
    Reduce a phone number to 10 digits, or return None if it isn't one.

    This is our strongest deduplication signal - two records sharing a phone
    number are almost always the same organization.

    >>> normalize_phone("(602) 555-0100")
    '6025550100'
    >>> normalize_phone("1-602-555-0100 ext 4")
    '6025550100'
    >>> normalize_phone("n/a")
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    # Strip a leading US country code
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    # Reject obvious placeholders (0000000000, 1111111111, etc.)
    if len(set(digits)) <= 1:
        return None
    return digits


def format_phone(digits):
    """Render 10 digits as 602-555-0100 for display."""
    if not digits or len(digits) != 10:
        return None
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"


# --------------------------------------------------------------------------
# Counties
# --------------------------------------------------------------------------

# Expanded so "ST LOUIS", "SAINT LOUIS" and "St. Louis" all collapse together.
_COUNTY_PREFIX_FIXES = [
    (r"^SAINT\s+", "ST. "),
    (r"^ST\s+", "ST. "),
    (r"^STE\s+", "STE. "),
]

_COUNTY_SUFFIXES = [
    " COUNTY", " PARISH", " BOROUGH", " CENSUS AREA",
    " MUNICIPALITY", " CITY AND BOROUGH",
]


def normalize_county(value):
    """
    Canonical uppercase county name.

    Drops the trailing 'County'/'Parish'/etc., normalizes Saint/St variants,
    and strips punctuation noise - EXCEPT the period in 'ST.' and the
    apostrophe in "PRINCE GEORGE'S", both of which we keep so the values
    match what we wrote in metros.json.

    >>> normalize_county("St. Louis County")
    'ST. LOUIS'
    >>> normalize_county("SAINT LOUIS")
    'ST. LOUIS'
    >>> normalize_county("Miami-Dade")
    'MIAMI-DADE'
    >>> normalize_county("Prince George's County")
    "PRINCE GEORGE'S"
    """
    if not value:
        return None
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    s = s.upper().strip()
    s = re.sub(r"\s+", " ", s)

    for suffix in _COUNTY_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()

    # Normalize Saint variants before we touch punctuation
    for pattern, replacement in _COUNTY_PREFIX_FIXES:
        s = re.sub(pattern, replacement, s)

    # Keep letters, digits, spaces, hyphens, apostrophes, and periods only
    s = re.sub(r"[^A-Z0-9 \-'.]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# --------------------------------------------------------------------------
# Organization names
# --------------------------------------------------------------------------

_ORG_NOISE = [
    r"\bLLC\b", r"\bL\.L\.C\.?", r"\bINC\b", r"\bINC\.", r"\bCORP\b",
    r"\bCORPORATION\b", r"\bCO\b", r"\bLP\b", r"\bLLP\b", r"\bPC\b",
    r"\bPLLC\b", r"\bLTD\b", r"\bDBA\b", r"\bTHE\b",
]


def normalize_org_name(value):
    """
    Canonical form of an organization name, for deduplication only.

    Never display this - it deliberately destroys information. Always keep the
    original name for showing to users.

    >>> normalize_org_name("Hospice of the Valley, Inc.")
    'HOSPICE OF VALLEY'
    """
    if not value:
        return None
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    s = s.upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    for pattern in _ORG_NOISE:
        s = re.sub(pattern, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# --------------------------------------------------------------------------
# Text and slugs
# --------------------------------------------------------------------------

def slugify(value, max_length=80):
    """
    URL-safe slug. Matches the pattern enforced by data/schema.json.

    >>> slugify("Hospice of the Valley - Child Loss (Phoenix)")
    'hospice-of-the-valley-child-loss-phoenix'
    """
    if not value:
        return None
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if len(s) > max_length:
        s = s[:max_length].rsplit("-", 1)[0]
    return s or None


def clean_text(value):
    """Collapse whitespace and strip. Returns None for empty or placeholder values."""
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    if not s or s.lower() in {"n/a", "na", "none", "null", "-", "not available"}:
        return None
    return s


def normalize_zip(value):
    """Return a 5-digit ZIP, handling ZIP+4 and lost leading zeros."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    if len(digits) == 9:          # ZIP+4 arrived without the hyphen
        digits = digits[:5]
    if len(digits) < 5:           # a leading zero was eaten by a spreadsheet
        digits = digits.zfill(5)
    return digits[:5] if len(digits) >= 5 else None


def normalize_state(value):
    """Two-letter uppercase state code, or None."""
    if not value:
        return None
    s = re.sub(r"[^A-Za-z]", "", str(value)).upper()
    return s if len(s) == 2 else None
