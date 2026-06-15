"""tests/test_extract.py — Tests for the rules-based extractor (local.py)."""

import pytest
from backend.extraction.local import (
    _find_header_rows,
    _map_columns_from_headers,
    _match_field,
    _is_total_row,
    _is_empty_row,
    _process_table,
    extract_items,
)


class TestMatchField:
    """Unit tests for header keyword matching."""

    def test_exact_match_model(self):
        assert _match_field("model") == "model"
        assert _match_field("model no") == "model"
        assert _match_field("model no.") == "model"

    def test_exact_match_brand(self):
        assert _match_field("brand") == "brand"
        assert _match_field("manufacturer") == "brand"

    def test_exact_match_description(self):
        assert _match_field("description") == "description"
        assert _match_field("desc") == "description"

    def test_exact_match_unit_price(self):
        assert _match_field("unit price") == "unit_price"
        assert _match_field("unit cost") == "unit_price"

    def test_substring_match(self):
        assert _match_field("item model number") == "model"

    def test_no_match(self):
        assert _match_field("randomtext") is None

    def test_empty_input(self):
        assert _match_field("") is None
        assert _match_field(None) is None


class TestRowClassification:
    """Tests for row type detection."""

    def test_empty_row(self):
        assert _is_empty_row([None, None, None]) is True
        assert _is_empty_row(["", "", ""]) is True

    def test_non_empty_row(self):
        assert _is_empty_row(["a", None, ""]) is False

    def test_total_row(self):
        assert _is_total_row(["", "Total", "$100"]) is True
        assert _is_total_row(["", "Subtotal:", "$50"]) is True
        assert _is_total_row(["", "Item A", "$100"]) is False


class TestFindHeaderRows:
    """Tests for header row detection."""

    def test_no_rows(self):
        end, conf = _find_header_rows([])
        assert end == 0

    def test_single_header_row(self):
        rows = [
            ["Item", "Brand", "Model", "Description", "Qty", "Unit Price"],
            ["1", "Bosch", "ABC-123", "Sensor", "10", "$25.00"],
        ]
        end, conf = _find_header_rows(rows)
        assert end >= 1
        assert conf > 0

    def test_multi_row_header(self):
        rows = [
            ["Item No", "Product", "", "", ""],
            ["", "Brand", "Model", "Qty", "Price"],
            ["1", "Bosch", "X1", "5", "$100"],
        ]
        end, conf = _find_header_rows(rows)
        assert end >= 1


class TestProcessTable:
    """Tests for end-to-end table processing."""

    def test_simple_table(self):
        table = {
            "rows": [
                ["Item", "Brand", "Model", "Description", "Qty", "Unit Price"],
                ["1", "Bosch", "ABC-123", "Pressure Sensor", "10", "25.00"],
                ["2", "Siemens", "DEF-456", "Temperature Probe", "5", "50.00"],
            ],
            "strategy": "text",
        }
        items, log = _process_table(table, 1, [], "auto")
        assert len(items) == 2
        assert items[0]["brand"] == "Bosch"
        assert items[0]["model"] == "ABC-123"
        assert items[1]["brand"] == "Siemens"

    def test_empty_table(self):
        table = {"rows": [], "strategy": "text"}
        items, log = _process_table(table, 1, [], "auto")
        assert len(items) == 0
        assert log["skipped_reason"] == "empty"

    def test_total_row_excluded(self):
        table = {
            "rows": [
                ["Item", "Brand", "Model", "Qty", "Unit Price"],
                ["1", "Bosch", "X1", "10", "25.00"],
                ["", "", "Total", "", "250.00"],
            ],
            "strategy": "text",
        }
        items, log = _process_table(table, 1, [], "auto")
        assert len(items) == 1

    def test_metadata_table_skipped(self):
        table = {
            "rows": [
                ["Vendor: ABC Corp"],
                ["PO No: 12345"],
            ],
            "strategy": "text",
        }
        items, log = _process_table(table, 1, [], "auto")
        assert len(items) == 0
        assert log["skipped_reason"] == "metadata_table"


class TestExtractItems:
    """Integration test for extract_items with parse_result format."""

    def test_basic_extraction(self):
        parse_result = {
            "parsers": {
                "pdfplumber": {
                    "pages": [
                        {
                            "page": 1,
                            "tables": [
                                {
                                    "rows": [
                                        ["Item", "Brand", "Model", "Description", "Qty", "Unit Price"],
                                        ["1", "Bosch", "ABC-123", "Pressure Sensor", "10", "25.00"],
                                    ],
                                    "strategy": "text",
                                }
                            ],
                        }
                    ]
                }
            }
        }
        result = extract_items(parse_result, filename="test.pdf")
        assert len(result["items"]) == 1
        assert result["items"][0]["brand"] == "Bosch"
        assert result["filename"] == "test.pdf"

    def test_no_tables(self):
        parse_result = {
            "parsers": {
                "pdfplumber": {
                    "pages": [{"page": 1, "tables": []}]
                }
            }
        }
        result = extract_items(parse_result)
        assert len(result["items"]) == 0

    def test_empty_parse_result(self):
        result = extract_items({})
        assert len(result["items"]) == 0
