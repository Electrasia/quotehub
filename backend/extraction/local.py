"""
backend/extraction/local.py — Rules-based quotation item extractor.

Reads the structured output of parse_pdf() (pdfplumber text + tables)
and produces the same JSON shape as the LLM output:
{filename, supplier, date, currency, document_type, items[]}.

No external dependencies beyond standard library.

This module is part of the extraction package and can be used standalone
or as a fallback in the extraction router.
"""
import re
from datetime import datetime


# -----------------------------------------------------------------------------
# Header keyword dictionary.
#
# Lowercase, no spaces. Longest match wins. Keywords are intentionally
# conservative — we want to be sure before we commit a field assignment.
# -----------------------------------------------------------------------------
FIELD_KEYWORDS = {
    "item_number": (
        "item no", "item no.", "item number", "item #", "item#",
        "line no", "line no.", "line number", "line #",
        "pos.", "pos", "position",
        "seq.", "seq", "sequence",
        "item", "no.", "no", "#",
    ),
    "model": (
        "model no", "model no.", "model number", "model #",
        "part no", "part no.", "part number", "part #", "part#",
        "p/n", "pn",
        "article no", "article no.", "article number",
        "cat no", "cat no.", "catalogue no", "product code",
        "manufacturer part", "mfr part", "mfr p/n", "mfr pn",
        "code", "ref", "ref.", "reference",
        "sku", "item code", "model",
    ),
    "brand": (
        "brand", "brand name", "manufacturer", "mfr", "mfg",
        "make", "oem", "vendor",
    ),
    "description": (
        "description", "desc", "desc.", "item description",
        "product description", "product name",
        "specification", "spec", "specs", "specifications",
        "details", "product", "product details",
        "nomenclature", "description of goods",
        "goods description", "item name", "name of goods",
        "goods", "item",
    ),
    "quantity": (
        "quantity", "qty", "qty.", "q'ty", "qnty", "qty ordered",
        "order qty", "ordered qty", "quantity ordered", "qty (pcs)",
    ),
    "unit": (
        "unit", "uom", "u/m", "um", "u.mea.", "unit of measure",
        "measure", "unit type",
    ),
    "unit_price": (
        "unit price", "unit cost", "unit rate", "unit amount",
        "unit value", "price/unit", "per unit", "rate/unit",
        "unit pr", "unit pr.", "price per", "cost per",
        "unit",
    ),
    "total": (
        "total amount", "line total", "line amount",
        "extended price", "ext. price", "ext price", "extended",
        "subtotal", "sub total", "sub-total",
        "total", "amount", "amt", "amt.",
    ),
    "currency": (
        "currency", "cur", "cur.", "ccy",
    ),
    "remark": (
        "remarks", "remark", "note", "notes", "comment", "comments",
    ),
    "delivery": (
        "delivery", "lead time", "need time", "availability",
        "delivery time", "delivery date", "stock status", "stock",
    ),
}


# Content-pattern regexes for column-type scoring
# A "price" must have: a decimal point, a thousands separator (comma or space),
# a currency symbol, or a currency code. Pure integers (1, 2, 3) are NOT prices.
PRICE_PATTERN = re.compile(
    r"^\s*[\$€£¥￥]\s*\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?\s*$|"   # $1,157.00 / $25,780.00
    r"^\s*\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?\s*$|"                  # 1,157.00 (no symbol)
    r"^\s*[\$€£¥￥]\s*\d+(?:\.\d+)?\s*$|"                          # $25.50 or $25
    r"^\s*\d+\.\d{2}\s*$|"                                         # 25.50 / 0.00
    r"^\s*(?:HKD|USD|EUR|GBP|CNY|RMB|JPY|HK)\s+[\d,\.]+\s*$",      # HKD 1,157.00
    re.IGNORECASE,
)
QUANTITY_PATTERN = re.compile(r"^\s*\d+(?:\.\d+)?\s*$")
INT_PATTERN = re.compile(r"^\s*\d+\s*$")


# -----------------------------------------------------------------------------
# Header normalization + matching
# -----------------------------------------------------------------------------

def _normalize_header_cell(text):
    """Lowercase, strip, remove parenthetical annotations like (HKD)."""
    if text is None:
        return ""
    s = str(text)
    s = re.sub(r"\([^)]*\)", "", s)         # (HKD), (USD), etc.
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def _match_field(header_text):
    """Return canonical field name for a header cell, or None.

    Tries in order:
      1. Exact match (after stripping leading digits and trailing colons)
      2. Substring match: cell contains keyword, or keyword contains cell
         (prefers longest keyword)
    """
    if not header_text:
        return None
    h = header_text.strip()
    h = re.sub(r"^\d+\.?\s*", "", h)   # strip leading "1." or "1 "
    h = h.rstrip(":").strip().lower()
    if not h:
        return None

    # 1. Exact match (with optional trailing dot)
    for field, keywords in FIELD_KEYWORDS.items():
        for kw in keywords:
            if h == kw or h == kw + "." or h == kw + ":":
                return field

    # 2. Substring match — prefer the longest keyword
    candidates = []
    for field, keywords in FIELD_KEYWORDS.items():
        for kw in keywords:
            if not kw:
                continue
            if kw in h or h in kw:
                candidates.append((len(kw), field, kw))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return None


# -----------------------------------------------------------------------------
# Header row detection
# -----------------------------------------------------------------------------

def _find_header_rows(rows, max_header_rows=6):
    """Find the header row(s) in a table.

    Returns (header_end_idx, confidence) where header_end_idx is the index
    of the first data row (i.e. header = rows[0:header_end_idx]).
    """
    if not rows:
        return 0, 0.0

    # Try each candidate header depth and pick the one with the best
    # field-match rate.
    best = (1, 0.0)  # default: row 0
    for n in range(1, min(max_header_rows + 1, len(rows) + 1)):
        header_rows = rows[:n]
        matches = 0
        total = 0
        for hr in header_rows:
            for c in hr:
                if c is None:
                    continue
                s = str(c).strip()
                if not s:
                    continue
                # Skip pure currency annotations
                if re.fullmatch(r"\(?\s*(HKD|USD|EUR|GBP|CNY|RMB|JPY)\s*\)?", s, re.IGNORECASE):
                    continue
                total += 1
                if _match_field(_normalize_header_cell(c)):
                    matches += 1
        rate = matches / total if total else 0
        if rate >= 0.5 and rate > best[1]:
            best = (n, rate)
    return best


def _is_merged_header_cell(cell_text):
    """Detect a header cell that contains multiple field keywords
    (e.g. pdfplumber captured the entire header row as one cell:
    'ITEM BRAND MODEL DESCRIPTIONS QTY UNIT PRICE FOB')."""
    if not cell_text:
        return False
    norm = _normalize_header_cell(cell_text)
    fields_matched = set()
    for field, keywords in FIELD_KEYWORDS.items():
        for kw in keywords:
            if len(kw) >= 3 and kw in norm:
                fields_matched.add(field)
                break
    return len(fields_matched) >= 3


def _map_columns_from_headers(header_rows):
    """Given header rows, return {col_idx: field_name}.

    Looks at each cell in each header row independently. If a cell's
    text matches a field, and that field is not already claimed by an
    earlier cell in the same column, assign it.

    If a cell's text contains 3+ field keywords (a "merged header"),
    skip it — content scoring will pick up that column.
    """
    if not header_rows:
        return {}
    n_cols = max((len(r) for r in header_rows), default=0)
    col_to_field = {}
    used_fields = set()
    for col_idx in range(n_cols):
        # Try each header row, take the first one that matches
        for hr in header_rows:
            if col_idx >= len(hr):
                continue
            raw = hr[col_idx]
            if raw is None:
                continue
            text = _normalize_header_cell(raw)
            if not text:
                continue
            if _is_merged_header_cell(raw):
                # Skip — this column's header is unreliable
                continue
            field = _match_field(text)
            if field and field not in used_fields:
                col_to_field[col_idx] = field
                used_fields.add(field)
                break
    return col_to_field


# -----------------------------------------------------------------------------
# Content-pattern scoring (refine column mapping)
# -----------------------------------------------------------------------------

def _score_columns_by_content(rows, col_to_field, header_end_idx):
    """For each column, score how well its content matches each field type.
    Reassign columns whose content strongly suggests a different field than
    the header.
    """
    data_rows = rows[header_end_idx:]
    if not data_rows:
        return col_to_field

    n_cols = max((len(r) for r in rows), default=0)
    col_scores = {}  # col_idx -> {field: score 0..1}

    for col_idx in range(n_cols):
        col_scores[col_idx] = {}
        cells = []
        for row in data_rows:
            if col_idx < len(row):
                v = row[col_idx]
                cells.append("" if v is None else str(v).strip())
        non_empty = [c for c in cells if c]
        if len(non_empty) < 2:
            continue

        n = len(non_empty)
        # Price columns
        price_hits = sum(1 for c in non_empty if PRICE_PATTERN.match(c))
        col_scores[col_idx]["unit_price"] = price_hits / n
        col_scores[col_idx]["total"] = price_hits / n * 0.85

        # Item number column (pure integers, short). Strongly position-biased:
        # the leftmost all-integer column is item_number, mid-table all-integer
        # columns are quantity.
        item_hits = sum(1 for c in non_empty if INT_PATTERN.match(c) and len(c) <= 5)
        item_score = item_hits / n
        # Apply position penalty: leftmost = strong, mid = weak
        if col_idx == 0:
            item_score = item_score * 1.0  # full strength
        elif col_idx <= 2:
            item_score = item_score * 0.5  # half strength
        else:
            item_score = item_score * 0.2  # weak (mid/right columns)
        col_scores[col_idx]["item_number"] = item_score

        # Quantity column (small integers or decimal). Strong everywhere
        # except where it would steal from a confident item_number.
        qty_hits = sum(1 for c in non_empty if QUANTITY_PATTERN.match(c) and len(c) <= 6)
        col_scores[col_idx]["quantity"] = qty_hits / n

        # Description: multi-word, longer text
        desc_hits = sum(1 for c in non_empty if " " in c and len(c) >= 5)
        col_scores[col_idx]["description"] = desc_hits / n

        # Model: contains both digits and letters, no spaces, short
        model_hits = sum(
            1 for c in non_empty
            if re.search(r"\d", c) and re.search(r"[a-zA-Z]", c)
            and " " not in c and 2 <= len(c) <= 40
        )
        col_scores[col_idx]["model"] = model_hits / n

        # Brand: single short word, no digits
        brand_hits = sum(
            1 for c in non_empty
            if re.fullmatch(r"[A-Za-z][A-Za-z\.\-]{1,19}", c) and len(c) <= 20
        )
        col_scores[col_idx]["brand"] = brand_hits / n

        # Unit: very short strings (not pure numbers, not prices)
        unit_hits = sum(
            1 for c in non_empty
            if 1 <= len(c) <= 6
            and not QUANTITY_PATTERN.match(c)
            and not PRICE_PATTERN.match(c)
        )
        col_scores[col_idx]["unit"] = unit_hits / n * 0.6

    # Two-pass approach:
    # 1. Fill in columns that didn't have a header match (use content scoring)
    # 2. For columns with a header match, override ONLY if content clearly
    #    disagrees: best content field >= 0.8 AND current header's content
    #    score < 0.3. This handles cases like:
    #      - QSC: header matched "description" (merged cell) but content is
    #        all integers → override to "item_number"
    #      - Q20260602: header "ITEM" matched "item_number" but content is
    #        long product names → override to "description"
    #    But it does NOT override when ambiguous (e.g. item_number vs quantity
    #    where both score 1.0).
    new_map = dict(col_to_field)
    used_fields = set(new_map.values())

    # Pass 1: fill unassigned columns
    for col_idx, scores in col_scores.items():
        if not scores:
            continue
        if col_idx in new_map:
            continue
        sorted_fields = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        threshold = 0.5
        chosen_field = None
        for field, score in sorted_fields:
            if score < threshold:
                break
            if field not in used_fields:
                chosen_field = field
                break
        if not chosen_field:
            continue
        new_map[col_idx] = chosen_field
        used_fields.add(chosen_field)

    # Pass 2: override if content clearly disagrees with header
    for col_idx, scores in col_scores.items():
        if not scores:
            continue
        current_field = new_map.get(col_idx)
        if not current_field:
            continue
        # Best content field
        best_field, best_score = max(scores.items(), key=lambda x: x[1])
        if best_field == current_field:
            continue
        if best_score < 0.8:
            continue
        # Current field's content score
        current_score = scores.get(current_field, 0)
        if current_score >= 0.3:
            continue
        # Override — but only if the new field is unclaimed
        if best_field in used_fields:
            # Special case: the only "competing" column is the same content
            # (e.g. two description columns). Allow override.
            competing_cols = [c for c, f in new_map.items()
                              if f == best_field and c != col_idx]
            if not competing_cols:
                # Field is in used_fields from somewhere, but no actual
                # column holds it. Unstick.
                used_fields.discard(best_field)
            else:
                continue
        # Apply override
        used_fields.discard(current_field)
        new_map[col_idx] = best_field
        used_fields.add(best_field)

    return new_map


# -----------------------------------------------------------------------------
# User override: which column should be the model field?
#
# Per-document choice passed in as model_source parameter:
#   - "auto"    : use the auto-detected column (default; no change)
#   - "model"   : use the column whose header is "Model" / "Model No" etc.
#   - "part_no" : use the column whose header is "Part" / "Part No" / "P/N" etc.
# The previously-assigned "model" column gets demoted to "description" so
# its content is preserved (extract_items concatenates duplicates).
# -----------------------------------------------------------------------------

_MODEL_HEADER_KWS = (
    "model", "model no", "model no.", "model number", "model #",
)
_PART_NO_HEADER_KWS = (
    "part", "part no", "part no.", "part number", "part #", "part#",
    "p/n", "pn", "p. n.", "p.n.", "part code", "partcode", "part no.",
)


def _apply_model_source_override(col_to_field, header_rows, model_source):
    """Reassign the model field to a user-chosen column.

    Returns a new col_to_field dict (or the original if no change applies).
    """
    if model_source == "auto" or not header_rows:
        return col_to_field

    if model_source == "model":
        target_kws = _MODEL_HEADER_KWS
    elif model_source == "part_no":
        target_kws = _PART_NO_HEADER_KWS
    else:
        return col_to_field

    # Find a column whose header matches the target keyword.
    # Check every header row, in order; first match wins.
    # Skip merged-header cells (where pdfplumber captured the whole row
    # as one cell, e.g. "ITEM BRAND MODEL DESCRIPTIONS QTY...") — those
    # are unreliable indicators of which column is which.
    target_col = None
    for hr in header_rows:
        for col_idx, cell in enumerate(hr):
            if cell is None:
                continue
            if _is_merged_header_cell(cell):
                continue
            text = _normalize_header_cell(cell)
            if not text:
                continue
            # Exact match (with optional trailing dot/colon)
            for kw in target_kws:
                if text == kw or text == kw + "." or text == kw + ":":
                    target_col = col_idx
                    break
            if target_col is not None:
                break
            # Substring match (only for keywords >= 3 chars)
            for kw in target_kws:
                if len(kw) >= 3 and (kw in text or text in kw):
                    target_col = col_idx
                    break
            if target_col is not None:
                break
        if target_col is not None:
            break

    if target_col is None:
        return col_to_field  # no matching column — silently fall back to auto

    # Find the currently-assigned model column (if any)
    current_model_col = None
    for col, field in col_to_field.items():
        if field == "model":
            current_model_col = col
            break

    if target_col == current_model_col:
        return col_to_field  # already correct

    # Build the new mapping
    new_map = dict(col_to_field)

    # Demote the previous model column to "description" so its content is kept.
    # If there's already a description column, the two will be concatenated
    # at extraction time.
    if current_model_col is not None and current_model_col != target_col:
        new_map.pop(current_model_col, None)
        new_map[current_model_col] = "description"

    # Free the target column if it was mapped to something else
    if target_col in new_map and new_map[target_col] != "model":
        # Drop the old field assignment on target_col
        del new_map[target_col]

    # Set target_col as "model"
    new_map[target_col] = "model"

    return new_map


# -----------------------------------------------------------------------------
# Cell value normalization
# -----------------------------------------------------------------------------

def _parse_cell(field, value):
    """Normalize a cell value for the given field."""
    if value is None:
        return ""
    s = str(value)
    if not s.strip():
        return ""

    if field == "model":
        # Strip leading labels like "P/N:" or "Model:"
        s = re.sub(
            r"^(p/?n|model|ref|ref\.|code|part\s*no\.?)\s*[:：]?\s*",
            "", s, flags=re.IGNORECASE,
        ).strip()
        return s.strip()

    if field in ("unit_price", "total"):
        return _parse_price(s)

    if field == "quantity":
        s = s.replace(",", "").strip()
        try:
            num = float(s)
            if num == int(num):
                return str(int(num))
            return str(num)
        except (ValueError, TypeError):
            return s.strip()

    if field == "unit":
        s = s.lower().strip().rstrip(".")
        # Normalize plurals
        singular = {
            "pcs": "pc", "pc": "pc", "piece": "pc", "pieces": "pc",
            "ea": "ea", "each": "ea",
            "set": "set", "sets": "set",
            "box": "box", "boxes": "box",
            "roll": "roll", "rolls": "roll",
            "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
            "pair": "pair", "pairs": "pair",
            "nos": "pc", "no": "pc",
        }
        return singular.get(s, s)

    if field == "brand":
        # First word only, capitalize properly
        s = s.strip()
        return s.split()[0] if s else ""

    if field == "description":
        s = re.sub(r"\s+", " ", s).strip()
        return s.strip(".,;:'\"")

    if field == "remark":
        s = re.sub(r"\s+", " ", s).strip()
        return s

    if field == "item_number":
        return s.strip()

    return s.strip()


def _parse_price(s):
    """Strip currency symbols, normalize number format (X,XXX.XX)."""
    original = s.strip()
    # Remove common currency markers
    s = s.replace("HK$", "").replace("US$", "")
    s = s.replace("$", "").replace("€", "").replace("£", "")
    s = s.replace("¥", "").replace("￥", "")
    s = re.sub(
        r"\b(HKD|USD|EUR|GBP|CNY|RMB|JPY|HK|US|MOP|MOP\s*|HKD\s*|USD\s*|MOP\$)\b",
        "", s, flags=re.IGNORECASE,
    )
    s = s.replace(" ", "")
    # Now try to parse as number
    # Handle "1,157.00" and "1.157,00" and "1157.00"
    s = s.replace(",", "")
    try:
        num = float(s)
        if num == int(num):
            return f"{int(num):,}"
        return f"{num:,.2f}"
    except (ValueError, TypeError):
        return original


# -----------------------------------------------------------------------------
# Row classification
# -----------------------------------------------------------------------------

def _is_empty_row(row):
    """Check if a table row is completely empty.
    
    Args:
        row: A list of cell values from the table.
        
    Returns:
        True if all cells are None or empty strings, False otherwise.
    """
    return all(c is None or not str(c).strip() for c in row)


def _is_total_row(row):
    """Check if a table row is a total/subtotal row.
    
    Args:
        row: A list of cell values from the table.
        
    Returns:
        True if the row contains total-related keywords, False otherwise.
    """
    for c in row:
        if c is None:
            continue
        s = str(c).lower().strip()
        if s in ("total", "total:", "subtotal", "subtotal:",
                 "grand total", "grand total:", "total amount",
                 "sub total", "sub-total"):
            return True
        if re.match(r"^total\s*[:：]", s):
            return True
    return False


def _is_section_header_row(row, col_to_field):
    """A section/category row: has some text content but no price and no total.

    Real items always have a unit_price or total. Section headers / category
    rows in PDFs like T1388 look like:
        "1 | Cable | CAT. 6 Cable | | | | |"  (no qty, no price, no total)
    and are followed by:
        "1.1 | 884035994/10 | CS31Z1 Grey ... | 300 | roll | $1,157 | $347,100 | ex-stock"

    The check below returns True for the first row (text present, no price)
    and False for the second (text AND price present).
    """
    if _is_empty_row(row):
        return False
    if _is_total_row(row):
        return False

    # If any price-like content is present, this is a real item, not a
    # section header. The "no model" check is no longer used because some
    # PDFs (T1388) put a category name in the model column on section rows.
    price_cols = [col for col, field in col_to_field.items()
                  if field in ("unit_price", "total")]
    for col in price_cols:
        if col < len(row) and row[col] is not None and str(row[col]).strip():
            return False  # has a price = real item

    # No price in any price column → it's a section header / category row.
    # Still require at least SOME text content to be safe.
    has_text = any(
        c is not None and str(c).strip()
        for c in row
    )
    return has_text


def _is_metadata_table(rows, col_to_field):
    """A table that is metadata (e.g. 'Vendor: ABC | PO No: 123' in 1 row)."""
    if len(rows) > 2:
        return False
    has_label = False
    for row in rows:
        for c in row:
            if c is None:
                continue
            s = str(c)
            if re.match(r"^\s*\w+:", s):
                has_label = True
                break
    if not has_label:
        return False
    # And no model/price data
    for row in rows:
        if not _is_empty_row(row):
            # Check for price-like content
            for c in row:
                if c is None:
                    continue
                if PRICE_PATTERN.match(str(c).strip()):
                    return False
    return True


def _is_garbage_table(rows):
    """A 'table' that is really just an email body, image caption, or other
    unstructured content flattened into one cell by pdfplumber.

    Heuristic: a real items table has multiple populated columns per row
    (item_number, model, description, qty, price, ...). A garbage table
    has at most one populated column — the rest are None or empty.

    Example: T1388's email-forwarded table on page 1 has 9 rows where most
    rows are one big text cell with the other 2 cells being None.
    """
    if not rows or len(rows) < 2:
        return False
    # For each row, count how many cells have any non-empty content.
    cells_per_row = []
    for row in rows:
        n = sum(1 for c in row if c is not None and str(c).strip())
        cells_per_row.append(n)
    # Find the max cells-per-row across all rows. A real table has
    # 4+ populated cells per row (item/model/desc/qty/price/total/remark).
    # A garbage table has 0-1.
    max_cells = max(cells_per_row) if cells_per_row else 0
    return max_cells <= 1


def _drop_phantom_columns(rows):
    """Remove columns where every data row is None/empty.

    Happens when pdfplumber misdetects column boundaries — e.g. the header
    'Item' gets split into ['Ite', 'm'] across two columns, leaving column
    1 as a phantom with all None data values (only the header cell has the
    fragment 'm'). Dropping the phantom re-aligns the column mapping.

    A column is phantom if BOTH:
    - its header is empty (None or all whitespace), AND
    - at least 80% of rows are empty AND the column has ≤2 cells of content

    The "header is empty" check protects real columns like "Remark" or
    "Note" which often have only 1 cell of content (the header) but are
    still valid columns.

    Returns a new list of rows with phantom columns removed.
    """
    if not rows:
        return rows
    n_cols = max((len(r) for r in rows), default=0)
    if n_cols == 0:
        return rows
    # We don't know which row is the header here, so use the heuristic
    # that a column is phantom only if NO cell across all rows has any
    # meaningful text (>2 chars). Catches 'm', 'Ite', 'I' fragments.
    phantom = set()
    for col in range(n_cols):
        nonempty_count = sum(
            1 for r in rows
            if col < len(r) and r[col] is not None and str(r[col]).strip()
        )
        empty_count = len(rows) - nonempty_count
        max_cell_len = 0
        for r in rows:
            if col < len(r) and r[col] is not None:
                max_cell_len = max(max_cell_len, len(str(r[col]).strip()))
        # Phantom = mostly empty AND no cell is a real header word
        if empty_count / max(len(rows), 1) >= 0.8 and nonempty_count <= 2 \
                and max_cell_len <= 2:
            phantom.add(col)
    if not phantom:
        return rows
    # Rebuild rows without phantom columns
    new_rows = []
    for r in rows:
        new_r = [c for i, c in enumerate(r) if i not in phantom]
        new_rows.append(new_r)
    return new_rows


def _extract_item_from_row(row, col_to_field):
    """Extract an item dict from a single row using the column mapping.

    Required for a valid item: (model OR description) AND (unit_price OR total).
    If only description is available (no model column), use the description
    as the model — this is the common case for many Asian quotation formats
    where the product name IS the model.

    If a field is assigned to multiple columns (e.g. a demoted model column
    and a description column both map to "description"), concatenate them.
    """
    item = {
        "brand": "",
        "model": "",
        "description": "",
        "quantity": "",
        "unit": "",
        "unit_price": "",
        "total": "",
        "remark": "",
    }
    for col_idx, field in col_to_field.items():
        if col_idx >= len(row):
            continue
        value = row[col_idx]
        if value is None or not str(value).strip():
            continue
        parsed = _parse_cell(field, value)
        if not parsed:
            continue
        # Use .get() in case the field (e.g. "item_number") isn't in the
        # fixed item dict — silently skip unknown fields.
        if field not in item:
            continue
        if item[field]:
            # Concatenate duplicate field assignments with a space
            # (newline if either side has one — preserves multiline structure)
            if "\n" in item[field] or "\n" in parsed:
                item[field] = f"{item[field]}\n{parsed}"
            else:
                item[field] = f"{item[field]} {parsed}"
        else:
            item[field] = parsed
    # Required: (model OR description) AND (unit_price OR total)
    if not item["model"] and not item["description"]:
        return None
    if not item["unit_price"] and not item["total"]:
        return None
    # If no model but has description, use description as model
    if not item["model"] and item["description"]:
        item["model"] = item["description"]
    return item


def _expand_multiline_cells(items):
    """Handle newlines in cell values: either JOIN them (text wrapping) or
    SPLIT them (multiple sub-items sharing the same row).

    The decision is based on whether NUMERIC fields (qty, unit_price, total)
    have multiple lines. If yes → SPLIT (each line is a sub-item). If no →
    JOIN (the newlines are just text wrapping, e.g. a model number that
    pdfplumber broke across two visual lines).

    Examples:
      - SMQ-26084 row: model="DLA140-\\n20W3000K-17D" with price "1,350.00"
        (1 line). The newlines are text wrapping → JOIN → model="DLA140-20W3000K-17D".
      - Commscope row: model="760237040\\n1375055-2" with qty "1\\n12" and
        price "$592.50\\nIncluded" → SPLIT into 2 sub-items.
      - Hotel Lisboa: model wraps to 3 lines, part_no/price/total have 2
        lines → SPLIT into 2 sub-items (numeric fields drive the count).
    """
    expanded = []
    for item in items:
        # Find fields with newlines
        multiline_fields = {
            k: v.split("\n") for k, v in item.items()
            if isinstance(v, str) and "\n" in v
        }
        if not multiline_fields:
            expanded.append(item)
            continue

        # Check whether any NUMERIC field is multi-line. That's the signal
        # for "this row is actually N sub-items, not wrapped text".
        numeric_fields = {"unit_price", "total", "quantity"}
        numeric_multiline = {
            f: lines for f, lines in multiline_fields.items()
            if f in numeric_fields and len(lines) >= 2
        }

        if not numeric_multiline:
            # No numeric field is multi-line → newlines are just text
            # wrapping. JOIN them with a single space in each multi-line
            # text field.
            joined_item = dict(item)
            for k, lines in multiline_fields.items():
                joined_item[k] = " ".join(l.strip() for l in lines).strip()
            expanded.append(joined_item)
            continue

        # Determine the canonical number of sub-items: the most common
        # line count across all multi-line fields. If there's a tie, prefer
        # the line count of numeric fields (price, total, qty).
        from collections import Counter
        line_counts = Counter(len(v) for v in multiline_fields.values())
        preferred_count = None
        max_count = max(line_counts.values())
        tied = [c for c, n in line_counts.items() if n == max_count]
        for c in tied:
            for f, lines in multiline_fields.items():
                if f in numeric_fields and len(lines) == c:
                    preferred_count = c
                    break
            if preferred_count:
                break
        if preferred_count is None:
            preferred_count = tied[0]

        if preferred_count < 2:
            expanded.append(item)
            continue

        # Split: for each line i, build an item with the i-th value of each
        # multi-line field (truncating to preferred_count). For text fields
        # that have MORE lines (like wrapped descriptions), merge extra lines
        # into the appropriate sub-item.
        for i in range(preferred_count):
            new_item = dict(item)
            for k, lines in multiline_fields.items():
                if len(lines) == preferred_count:
                    new_item[k] = lines[i].strip()
                elif len(lines) > preferred_count:
                    # This field has more lines (text wrapping). Distribute
                    # extra lines evenly. If preferred_count=2 and lines=3,
                    # give line 0 to sub-item 0 and lines 1,2 to sub-item 1.
                    if i == preferred_count - 1:
                        new_item[k] = " ".join(
                            l.strip() for l in lines[i:]
                        ).strip()
                    else:
                        new_item[k] = lines[i].strip()
                else:
                    new_item[k] = lines[i].strip() if i < len(lines) else ""
            # If the split resulted in a row with no price AND no model, skip
            if not new_item.get("model") and not new_item.get("unit_price") \
                    and not new_item.get("total"):
                continue
            if not new_item.get("unit_price") and not new_item.get("total"):
                continue
            if not new_item.get("model"):
                continue
            expanded.append(new_item)
    return expanded


# -----------------------------------------------------------------------------
# Main entry points
# -----------------------------------------------------------------------------

def _process_table(table, page_num, warnings, model_source="auto"):
    """Process a single table. Returns (items, table_log)."""
    rows = table.get("rows", []) or []
    if not rows:
        return [], {"rows_scanned": 0, "items_extracted": 0, "skipped_reason": "empty"}

    # Check for metadata tables (1-2 rows of "Label: Value")
    if _is_metadata_table(rows, {}):
        return [], {"rows_scanned": len(rows), "items_extracted": 0,
                    "skipped_reason": "metadata_table"}

    # Skip "garbage" tables (e.g. an email body flattened into one big cell).
    # The _find_header_rows heuristic would otherwise misfire on them.
    if _is_garbage_table(rows):
        return [], {"rows_scanned": len(rows), "items_extracted": 0,
                    "skipped_reason": "garbage_table"}

    # Drop phantom columns (all-empty columns from a misdetected column
    # boundary, e.g. ['Ite', 'm'] instead of ['Item']).
    rows = _drop_phantom_columns(rows)

    # Find header rows
    header_end, confidence = _find_header_rows(rows)
    header_rows = rows[:header_end]

    # Map columns from headers
    col_to_field = _map_columns_from_headers(header_rows)

    # Refine mapping with content scoring. When header_end=1 and confidence=0,
    # no real header was found — use all rows for scoring so columns with only
    # 1-2 data rows can still be detected (e.g. continuation pages without a
    # repeated header row).
    score_header_end = 0 if (header_end <= 1 and confidence < 0.1) else header_end
    col_to_field = _score_columns_by_content(rows, col_to_field, score_header_end)

    # Apply per-document model source override (if any)
    col_to_field = _apply_model_source_override(col_to_field, header_rows, model_source)

    # Must have at least a model OR description column to extract items
    has_identifier = "model" in col_to_field.values() or "description" in col_to_field.values()
    if not has_identifier:
        return [], {
            "rows_scanned": len(rows),
            "items_extracted": 0,
            "skipped_reason": "no_model_or_description_column",
            "header_confidence": round(confidence, 2),
            "column_mapping": col_to_field,
        }

    # Extract items row by row
    items = []
    rows_scanned = 0
    for row in rows[header_end:]:
        rows_scanned += 1
        if _is_empty_row(row):
            continue
        if _is_total_row(row):
            continue
        if _is_section_header_row(row, col_to_field):
            continue
        item = _extract_item_from_row(row, col_to_field)
        if item:
            items.append(item)

    # Expand multi-line cells
    items = _expand_multiline_cells(items)

    log = {
        "rows_scanned": rows_scanned,
        "items_extracted": len(items),
        "header_rows": len(header_rows),
        "header_confidence": round(confidence, 2),
        "column_mapping": col_to_field,
    }
    return items, log


def extract_items(parse_result, source_text="", filename="", model_source="auto"):
    """Main entry point. Returns the full extraction result.

    Args:
        parse_result: Output of parser.parse_pdf()
        source_text: Full text of the PDF (for metadata extraction)
        filename: Original filename (for document type detection)
        model_source: Per-document override for which column is the "model"
            field. One of "auto" (default), "model", "part_no".
    """
    warnings = []
    pp = parse_result.get("parsers", {}).get("pdfplumber", {})
    pp_pages = pp.get("pages", [])

    all_items = []
    tables_log = []
    for page_data in pp_pages:
        page_num = page_data.get("page", 0)
        for t_idx, table in enumerate(page_data.get("tables", [])):
            items, t_log = _process_table(table, page_num, warnings, model_source)
            # Tag each item with its source page so the streaming endpoint
            # can group items per page for progress reporting.
            for it in items:
                it["page"] = page_num
            all_items.extend(items)
            tables_log.append({
                "page": page_num,
                "table_index": t_idx,
                "strategy": table.get("strategy", "?"),
                "items_extracted": len(items),
                **t_log,
            })

    # Filter out junk items: very long descriptions (>500 chars) usually mean
    # we accidentally picked up an email body or paragraph instead of a real
    # row. (e.g. T1388 page 1 has a single "row" that is the whole email body.)
    filtered = []
    for it in all_items:
        if len(it.get("description", "")) > 500 or len(it.get("model", "")) > 500:
            continue
        filtered.append(it)
    if len(filtered) < len(all_items):
        warnings.append(
            f"Filtered {len(all_items) - len(filtered)} junk item(s) with "
            "suspiciously long descriptions"
        )
    all_items = filtered

    # Deduplicate items that appear in multiple tables (e.g. T1388's items
    # table is detected on both page 1 and page 2). Key by (item_number,
    # model, qty, unit_price) — when duplicates are found, keep the version
    # with the longer (more complete) description.
    deduped = {}
    for it in all_items:
        key = (
            str(it.get("item_number", "")).strip(),
            str(it.get("model", "")).strip(),
            str(it.get("quantity", "")).strip(),
            str(it.get("unit_price", "")).strip(),
        )
        if key in deduped:
            existing = deduped[key]
            if len(it.get("description", "")) > len(existing.get("description", "")):
                deduped[key] = it
        else:
            deduped[key] = it
    if len(deduped) < len(all_items):
        warnings.append(
            f"Deduplicated {len(all_items) - len(deduped)} item(s) that appeared "
            "in multiple tables (same item_number + model + qty + price)"
        )
    all_items = list(deduped.values())

    # Metadata
    metadata = extract_metadata(source_text, filename, pp_pages, warnings)

    if not all_items and not warnings:
        warnings.append("No items extracted — file may be scanned or use unrecognized table format")

    return {
        "filename": filename,
        "supplier": metadata["supplier"],
        "date": metadata["date"],
        "currency": metadata["currency"],
        "document_type": metadata["document_type"],
        "items": all_items,
        "extraction_warnings": warnings,
        "model_source": model_source,
        "extraction_log": {
            "tables_processed": len(tables_log),
            "tables_log": tables_log,
            "total_items": len(all_items),
        },
    }


# -----------------------------------------------------------------------------
# Metadata extraction (supplier, date, currency, document_type)
# -----------------------------------------------------------------------------

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _try_dd_mm_yyyy(m):
    """Parse a date in DD/MM/YYYY or MM/DD/YYYY format.
    
    Args:
        m: A regex match object with groups (day, month, year).
        
    Returns:
        Date in YYYY-MM-DD format.
    """
    a, b, y = m.group(1), m.group(2), m.group(3)
    a, b = int(a), int(b)
    if a > 12:  # must be DD/MM
        return f"{y}-{b:02d}-{a:02d}"
    if b > 12:  # must be MM/DD
        return f"{y}-{a:02d}-{b:02d}"
    # Ambiguous — default DD/MM (matches our test set's 2026 docs)
    return f"{y}-{b:02d}-{a:02d}"


def _try_dd_mm_yy(m):
    """Parse a date in DD/MM/YY or MM/DD/YY format.
    
    Args:
        m: A regex match object with groups (day, month, year).
        
    Returns:
        Date in YYYY-MM-DD format.
    """
    a, b, y = m.group(1), m.group(2), m.group(3)
    a, b = int(a), int(b)
    y = int(y)
    y = 2000 + y if y < 50 else 1900 + y
    if a > 12:
        return f"{y}-{b:02d}-{a:02d}"
    if b > 12:
        return f"{y}-{a:02d}-{b:02d}"
    return f"{y}-{b:02d}-{a:02d}"


def _try_d_mon_yyyy(m):
    """Parse a date in DD-Mon-YYYY format (e.g., 15-Jan-2026).
    
    Args:
        m: A regex match object with groups (day, month abbreviation, year).
        
    Returns:
        Date in YYYY-MM-DD format, or None if month is invalid.
    """
    d, mon_s, y = m.group(1), m.group(2).lower(), m.group(3)
    mon = MONTHS.get(mon_s)
    if not mon:
        return None
    return f"{y}-{mon:02d}-{int(d):02d}"


def _try_mon_d_yyyy(m):
    """Parse a date in Mon-DD-YYYY format (e.g., Jan-15-2026).
    
    Args:
        m: A regex match object with groups (month abbreviation, day, year).
        
    Returns:
        Date in YYYY-MM-DD format, or None if month is invalid.
    """
    mon_s, d, y = m.group(1).lower(), m.group(2), m.group(3)
    mon = MONTHS.get(mon_s)
    if not mon:
        return None
    return f"{y}-{mon:02d}-{int(d):02d}"


DATE_PATTERNS = [
    # YYYY-MM-DD
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}"),
    # DD-MM-YYYY or MM-DD-YYYY
    (re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b"), _try_dd_mm_yyyy),
    # DD-MM-YY or MM-DD-YY
    (re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})\b"), _try_dd_mm_yy),
    # 1 Jan 2026 or 1 January 2026
    (re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b"), _try_d_mon_yyyy),
    # Jan 1, 2026 or January 1, 2026
    (re.compile(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b"), _try_mon_d_yyyy),
]

CURRENCY_MARKERS = [
    ("HKD", ["hkd", "hk$", "hk "]),
    ("USD", ["usd", "us$", "u.s.dollar"]),
    ("EUR", ["eur", "€"]),
    ("GBP", ["gbp", "£"]),
    ("CNY", ["cny", "rmb", "￥", "¥", "yuan"]),
    ("JPY", ["jpy", "¥"]),
]


def _normalize_date(s):
    """Try to parse a date string into YYYY-MM-DD. Returns None if can't."""
    for pattern, handler in DATE_PATTERNS:
        m = pattern.search(s)
        if m:
            try:
                result = handler(m)
                if result:
                    # Validate
                    datetime.strptime(result, "%Y-%m-%d")
                    return result
            except (ValueError, AttributeError):
                continue
    return None


def _detect_currency(text):
    """Find a currency marker in text. Returns the ISO code or None."""
    if not text:
        return None
    t = text.lower()
    # Specific checks first
    if "hkd" in t or "hk$" in t:
        return "HKD"
    if "usd" in t or "us$" in t or "u.s." in t:
        return "USD"
    if "cny" in t or "rmb" in t or "￥" in t or "yuan" in t:
        return "CNY"
    if "eur" in t or "€" in t:
        return "EUR"
    if "gbp" in t or "£" in t:
        return "GBP"
    if "jpy" in t or "¥" in t:
        return "JPY"
    # Bare $ is ambiguous — fall back to it only if nothing else matches
    if "$" in t:
        return "USD"
    return None


def _extract_supplier_from_text(text):
    """Scan page text for a company name.

    Strategy:
      1. Look for explicit labels: "From:", "Vendor:", "Supplier:", "SOLD TO:"
      2. Look for "Company:" label (often in terms/signature section)
      3. Look for company suffixes: "Ltd", "Limited", "Inc", "LLC", "Corp", "GmbH"
      4. Look for ALL-CAPS short names in the first 1500 chars (e.g. "QSC", "BOSCH")
    """
    if not text:
        return ""

    # 1. From: pattern (email forward)
    from_pat = re.compile(r"From:\s*([^<\n]+?)\s*(?:<|$)", re.IGNORECASE)
    m = from_pat.search(text[:1500])
    if m:
        return m.group(1).strip().rstrip(".,")

    # 1b. Email forward format: "Name<email@domain.com>" or "Name <email@domain.com>"
    #     Extract the name part. The company can be inferred from the email domain.
    email_pat = re.compile(
        r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\s*<[a-zA-Z0-9._+-]+@([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)>"
    )
    m = email_pat.search(text[:1500])
    if m:
        name = m.group(1).strip()
        # The email name is a person, not a company. Try to use the company
        # from the suffix regex (which might be later in the text) before
        # falling back to the person's name.
        # For now, return the person's name; the user can clean up later.
        return name

    # 2. Look for "Company: XXX" pattern (often in terms/signature section)
    company_pat = re.compile(
        r"Company\s*[:：]\s*([^\n|]+?)(?:\n|$)",
        re.IGNORECASE,
    )
    m = company_pat.search(text)
    if m:
        return m.group(1).strip().rstrip(".,")

    # 3. Find a name ending with a company suffix. Permissive about parens,
    #    &, and other punctuation between words. Case-insensitive because
    #    company names are often ALL CAPS in headers. Exclude newlines so
    #    we don't capture across table cell boundaries.
    suffix_re = re.compile(
        r"(?P<name>[A-Z][A-Za-z0-9&\.\-\(\) ]{2,100}?)"
        r"[ \t]+(?:Ltd\.?|Limited|LIMITED|Inc\.?|INC|LLC|Corp\.?|Corporation|Company"
        r"|Co\.?,?\s+Ltd|GmbH|S\.A\.|S\.r\.l\.)",
        re.MULTILINE,
    )
    matches = list(suffix_re.finditer(text))
    if matches:
        # Prefer matches that appear early in the text (likely the company
        # header, not a paragraph)
        best = min(matches, key=lambda m: m.start())
        return best.group("name").strip()

    # 4. ALL-CAPS names in the first 1500 chars. Common headers/words to skip.
    # Note: brand names like "QSC", "BOSCH", "TANNOY" are NOT in this list —
    # they might be the supplier.
    skip_words = {
        "DATE", "TO", "FROM", "FAX", "TEL", "PHONE", "EMAIL", "ATTN", "ATT",
        "REF", "RE", "SUBJECT", "QUOTATION", "INVOICE", "ORDER", "PURCHASE",
        "PROJECT", "PO", "NO", "NO.", "QTY", "QTY.", "QUANTITY", "PRICE",
        "TOTAL", "AMOUNT", "DESCRIPTION", "MODEL", "BRAND", "ITEM", "ITEMS",
        "PAGE", "PAGES", "FAX:", "TEL:", "EMAIL:", "ATTN:", "ATT:",
        "QUOTATION NO", "QUOTATION NO.", "QUOTATION NO:",
        "COMPANY", "ADDRESS", "ADD", "ADD:", "ADDR",
        "VENDOR", "SUPPLIER", "CUSTOMER", "BILL TO", "SHIP TO", "SOLD TO",
        "SOLD", "BUYER", "DEAR", "HELLO", "HI", "THANKS", "THANK",
        # Quotation number prefixes
        "SMQ", "QMS",
        # Currency / units
        "HKD", "USD", "EUR", "GBP", "CNY", "RMB", "JPY", "MOP", "NOS",
        "FOB", "EXW", "CIF", "HK", "US", "EU",  # Common suffix abbreviations
    }
    # Pattern for "Quotation No: ABC123" style — extract the part BEFORE the
    # colon as a possible supplier (e.g. "Quotation No. : SMQ-26084" → "SMQ"
    # is part of the quotation number, not a supplier)
    skip_if_after_label = re.compile(
        r"(?:Quotation\s*No\.?\s*[:：]?|PO\s*No\.?\s*[:：]?|"
        r"Invoice\s*No\.?\s*[:：]?|RFQ\s*[:：]?|"
        r"Quotation\s*Number\s*[:：]?|Ref\.?\s*[:：]?)",
        re.IGNORECASE,
    )
    # Skip ALL-CAPS words that appear after a buyer/customer label
    skip_buyer_prefix = re.compile(
        r"^\s*(?:To|Buyer|Customer|Bill\s*To|Ship\s*To|Client|Attention|Attn)\s*[:：]?\s*",
        re.IGNORECASE,
    )
    # Scan each line, find ALL-CAPS words
    candidates = []  # (length, word)
    for line in text[:1500].splitlines()[:40]:
        line = line.strip()
        if not line or len(line) > 60:
            continue
        # Skip lines that look like "Quotation No. : SMQ-26084" (the ALL-CAPS
        # word is the quotation number, not a supplier).
        if skip_if_after_label.search(line):
            continue
        # Skip lines that start with "To:" / "Buyer:" (the ALL-CAPS word is
        # the customer, not the supplier)
        if skip_buyer_prefix.match(line):
            continue
        # Find ALL-CAPS "words" (1-20 chars, optional & or .)
        for m in re.finditer(r"\b([A-Z][A-Z0-9\&\.\-]{1,19})\b", line):
            word = m.group(1)
            if word in skip_words:
                continue
            if word.isdigit():
                continue
            # Skip if the word contains a hyphen + digits (likely a ref number)
            if re.search(r"-\d", word):
                continue
            # Skip if the word has lowercase letters mixed in (it's a model no)
            if re.search(r"[a-z]", word) and re.search(r"[A-Z]", word[1:]):
                continue
            # Skip if surrounded by other lowercase letters (it's a regular word)
            start = m.start()
            end = m.end()
            if start > 0 and line[start-1].islower():
                continue
            if end < len(line) and line[end].islower():
                continue
            candidates.append((len(word), word, line))
    if candidates:
        # Pick the longest
        candidates.sort(reverse=True)
        # Also prefer the one that appears in a "PROJECT:" or similar context
        for length, word, line in candidates:
            if "PROJECT" in line.upper() or "VENDOR" in line.upper() or "FROM" in line.upper():
                return word
        # Otherwise just the longest
        return candidates[0][1]

    return ""


def _extract_supplier_from_metadata_table(pp_pages):
    """Look in the first page's metadata table (1-row label:value)."""
    if not pp_pages:
        return ""
    for t in pp_pages[0].get("tables", []):
        rows = t.get("rows", [])
        if len(rows) > 2:
            continue
        for row in rows:
            for c in row:
                if c is None:
                    continue
                s = str(c)
                # Look for "Vendor:" or "From:" or "Supplier:" or "SOLD TO:"
                m = re.search(
                    r"(?:Vendor|From|Supplier|SOLD\s*TO|Sold\s*to|Company)\s*[:：]\s*([^\n|]+?)(?:\s*\||\s*Addre|$)",
                    s, re.IGNORECASE,
                )
                if m:
                    return m.group(1).strip()
    return ""


def _extract_date_from_metadata_table(pp_pages):
    """Extract quotation date from metadata table in first page.
    
    Args:
        pp_pages: List of pdfplumber page results.
        
    Returns:
        Date string in YYYY-MM-DD format, or empty string if not found.
    """
    if not pp_pages:
        return ""
    for t in pp_pages[0].get("tables", []):
        for row in t.get("rows", []):
            for c in row:
                if c is None:
                    continue
                s = str(c)
                m = re.search(
                    r"(?:Date|Quotation\s*Date|Issued|Issue\s*Date|PO\s*Date)\s*[:：]\s*([^\n|]+)",
                    s, re.IGNORECASE,
                )
                if m:
                    date = _normalize_date(m.group(1))
                    if date:
                        return date
    return ""


def _extract_currency_from_table(pp_pages):
    """Extract currency symbol from metadata table in first page.
    
    Args:
        pp_pages: List of pdfplumber page results.
        
    Returns:
        Currency code (e.g., USD, EUR, HKD), or empty string if not found.
    """
    if not pp_pages:
        return ""
    for t in pp_pages[0].get("tables", []):
        for row in t.get("rows", []):
            for c in row:
                if c is None:
                    continue
                cur = _detect_currency(str(c))
                if cur:
                    return cur
    return ""


def _detect_document_type(filename, text):
    """Classify the document as QUO / PO / PL / unknown.

    Filename is checked first (more reliable), then page 1 text.
    """
    fn = (filename or "").lower()
    tx = (text or "")[:2000].lower()

    # Price List first (most specific)
    if any(kw in fn for kw in ("pricelist", "price_list", "price-list", "price list")):
        return "PL"
    if "price list" in tx or "pricelist" in tx or "price_list" in tx:
        return "PL"

    # Purchase Order
    if any(kw in fn for kw in ("purchase_order", "purchase-order", "po_", "-po-", " po ", "_po_")):
        return "PO"
    if "purchase order" in tx or "p.o." in tx or " po no" in tx or "po number" in tx:
        return "PO"

    # Quotation
    if any(kw in fn for kw in ("quotation", "quote", "q-", "qsc")):
        return "QUO"
    if "quotation" in tx or "quote" in tx or "offer" in tx:
        return "QUO"

    # Invoice / Packing List
    if "packing list" in tx or "packinglist" in tx:
        return "PL"
    if "invoice" in tx:
        return "INVOICE"

    return "unknown"


def extract_metadata(text, filename, pp_pages, warnings):
    """Extract supplier, date, currency, document_type."""
    supplier = _extract_supplier_from_metadata_table(pp_pages) or _extract_supplier_from_text(text)
    date = _extract_date_from_metadata_table(pp_pages) or _normalize_date(text[:2000] if text else "")
    currency = _extract_currency_from_table(pp_pages) or _detect_currency(text[:2000] if text else "")
    document_type = _detect_document_type(filename, text)

    if not supplier:
        warnings.append("Supplier not detected (no company name found in metadata table or first lines of text)")
    if not date:
        warnings.append("Date not detected (no date pattern matched in metadata table or first lines of text)")
    if not currency:
        warnings.append("Currency not detected (no currency marker found)")

    return {
        "supplier": supplier,
        "date": date or "",
        "currency": currency or "",
        "document_type": document_type,
    }
