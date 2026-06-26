"""
backend/suppliers.py — Supplier normalization and resolution.

This module provides the core normalization and resolution logic
for supplier names. It is used by the suppliers API routes and
the backfill migration.

STRICTLY READ-ONLY: resolve_supplier() never creates or modifies
database records. It only queries. Callers (such as the backfill
migration or API route handlers) decide what to do with unmatched names.
"""

import re

# ─── Normalization ───────────────────────────────────────────────────────────

COMMON_STOPWORDS = frozenset({
    "ltd", "limited",
    "inc", "incorporated",
    "co", "company",
    "corp", "corporation",
})


def get_meaningful_tokens(canonical_name: str) -> list[str]:
    """Return canonical_name tokens with common business suffixes removed.

    If all tokens are stopwords, returns the original tokens to avoid
    pathological empty-match cases.
    """
    tokens = canonical_name.split()
    meaningful = [t for t in tokens if t not in COMMON_STOPWORDS]
    return meaningful if meaningful else tokens

def normalize_name(raw: str | None) -> str:
    """Normalize a supplier name for matching.

    Steps:
    1. Handle **None** and empty input → return ``""``.
    2. Strip leading/trailing whitespace.
    3. Collapse internal whitespace to a single space.
    4. Lowercase.
    5. Strip punctuation except hyphens ``-`` and apostrophes ``'``.
    6. Strip leading/trailing whitespace again (after punctuation removal).

    This function is shared across all name-based matching in the suppliers
    subsystem: canonical names, aliases, brands, product types, and quotation
    ``normalized_supplier_name`` computation.

    Args:
        raw: Raw supplier name (or ``None``).

    Returns:
        Normalized string (``""`` if input was ``None``, empty, or whitespace-only).
    """
    if not raw:
        return ""
    s = raw.strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    s = s.lower()
    s = re.sub(r"[^\w\s'-]", "", s)
    return s.strip()


# ─── Resolution (strictly read-only) ────────────────────────────────────────

def resolve_supplier(db, raw_name: str) -> int | None:
    """Resolve a raw supplier name to a ``suppliers.id``.

    **STRICTLY READ-ONLY.**  This function never creates, updates, or
    deletes any database records.  It performs SELECT queries only.

    Resolution order:
    1. Normalise the input via :func:`normalize_name`.
    2. Match against ``suppliers.canonical_name`` (exact, indexed).
    3. Match against ``supplier_aliases.alias`` (exact, indexed).
    4. Return ``None`` if no match found.

    Args:
        db:     An active :class:`sqlite3.Connection` (e.g. from :func:`get_db`).
        raw_name: Raw supplier name to resolve.

    Returns:
        The matched ``suppliers.id``, or ``None`` if no match exists.
    """
    normalized = normalize_name(raw_name)
    if not normalized:
        return None

    # Step 1 — exact match on canonical_name (uses idx_suppliers_canonical_name)
    row = db.execute(
        "SELECT id FROM suppliers WHERE canonical_name = ?",
        (normalized,),
    ).fetchone()
    if row:
        return row["id"]

    # Step 2 — match via alias (uses idx_supplier_aliases_alias)
    row = db.execute(
        "SELECT sa.supplier_id FROM supplier_aliases sa WHERE sa.alias = ?",
        (normalized,),
    ).fetchone()
    if row:
        return row["supplier_id"]

    # Step 3 — no match
    return None
