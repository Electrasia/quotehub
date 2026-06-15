"""tests/test_extraction_pipeline.py — Mock-based tests for the extraction pipeline.

Tests the full extraction chain: router routing decisions, LLM calls,
fallback chain, and response parsing — all without a real AI server.
"""
import httpx
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# =============================================================================
# Router tests — mock internal functions to verify routing logic
# =============================================================================


class TestRouterRouting:
    """Test that extract_items_async routes to the correct extraction method
    based on parse_result content."""

    @pytest.fixture
    def text_pdf_parse_result(self):
        """A parse result with enough text to be classified as text PDF."""
        return {
            "pdf_path": "/tmp/test.pdf",
            "num_pages": 2,
            "parsers": {
                "pdfplumber": {
                    "pages": [
                        {"page": 1, "text": "QUOTATION NO: SMQ-26084\nSUPPLIER: ABC Corp\nItem Model Qty Price\n"},
                        {"page": 2, "text": "Item Model Qty Price\n1 BOSCH X1 10 $25\n"},
                    ],
                }
            },
        }

    @pytest.fixture
    def scanned_pdf_parse_result(self):
        """A parse result with minimal text (scanned PDF)."""
        return {
            "pdf_path": "/tmp/scanned.pdf",
            "num_pages": 2,
            "parsers": {
                "pdfplumber": {
                    "pages": [
                        {"page": 1, "text": " "},
                        {"page": 2, "text": ""},
                    ],
                }
            },
        }

    @pytest.fixture
    def xlsx_parse_result(self):
        """A parse result from an XLSX file."""
        return {
            "pdf_path": "/tmp/data.xlsx",
            "num_pages": 2,
            "parsers": {
                "xlsx": {"available": True},
                "pdfplumber": {
                    "pages": [
                        {"page": 1, "text": "Brand | Model | Qty | Unit Price\nBOSCH | X1 | 10 | 25.00\n"},
                        {"page": 2, "text": "Brand | Model | Qty | Unit Price\nSiemens | Y2 | 5 | 50.00\n"},
                    ],
                }
            },
        }

    @pytest.fixture
    def llm_result(self):
        """A successful Text LLM result."""
        from backend.extraction.router import ExtractionResult
        return ExtractionResult(
            items=[{"brand": "BOSCH", "model": "X1", "description": "Sensor",
                    "quantity": "10", "unit": "pc", "unit_price": "25.00", "total": ""}],
            supplier="ABC Corp",
            date="2026-06-15",
            currency="HKD",
            document_type="QUO",
            extraction_method="llm",
        )

    @pytest.fixture
    def vision_result(self):
        """A successful Vision LLM result."""
        from backend.extraction.router import ExtractionResult
        return ExtractionResult(
            items=[{"brand": "BOSCH", "model": "X1", "description": "Sensor",
                    "quantity": "10", "unit": "pc", "unit_price": "25.00", "total": ""}],
            supplier="ABC Corp",
            date="2026-06-15",
            currency="HKD",
            document_type="QUO",
            extraction_method="vision",
        )

    @pytest.fixture
    def local_result(self):
        """A successful local extraction result."""
        from backend.extraction.router import ExtractionResult
        return ExtractionResult(
            items=[{"brand": "BOSCH", "model": "X1", "description": "Sensor"}],
            extraction_method="local",
        )

    # load_config is imported INSIDE extract_items_async, so we patch at the
    # source module (backend.utils) rather than backend.extraction.router.
    @patch("backend.utils.load_config")
    async def test_text_pdf_uses_text_llm(self, mock_load_config, text_pdf_parse_result, llm_result):
        """Text PDF (avg_chars >= 50) should try Text LLM first."""
        mock_load_config.return_value = {"extraction_enabled": True}

        with patch("backend.extraction.router._try_text_llm", new_callable=AsyncMock) as mock_text, \
             patch("backend.extraction.router._try_vision_llm", new_callable=AsyncMock) as mock_vision, \
             patch("backend.extraction.router._run_local") as mock_local:
            mock_text.return_value = llm_result

            from backend.extraction.router import extract_items_async
            result = await extract_items_async(text_pdf_parse_result)

            mock_text.assert_awaited_once()
            mock_vision.assert_not_awaited()
            mock_local.assert_not_called()
            assert result.extraction_method == "llm"
            assert len(result.items) == 1
            assert result.supplier == "ABC Corp"

    @patch("backend.utils.load_config")
    async def test_scanned_pdf_uses_vision_llm(self, mock_load_config, scanned_pdf_parse_result, vision_result):
        """Scanned PDF (avg_chars < 50) should try Vision LLM first."""
        mock_load_config.return_value = {"extraction_enabled": True}

        with patch("backend.extraction.router._try_text_llm", new_callable=AsyncMock) as mock_text, \
             patch("backend.extraction.router._try_vision_llm", new_callable=AsyncMock) as mock_vision, \
             patch("backend.extraction.router._run_local") as mock_local:
            mock_text.return_value = None  # Text LLM skipped or fails
            mock_vision.return_value = vision_result

            from backend.extraction.router import extract_items_async
            result = await extract_items_async(scanned_pdf_parse_result)

            mock_text.assert_not_awaited()  # _get_pages_text returns empty → skip
            mock_vision.assert_awaited_once()
            mock_local.assert_not_called()
            assert result.extraction_method == "vision"

    @patch("backend.utils.load_config")
    async def test_xlsx_uses_text_llm(self, mock_load_config, xlsx_parse_result, llm_result):
        """XLSX files should use Text LLM with is_xlsx=True."""
        mock_load_config.return_value = {"extraction_enabled": True}

        with patch("backend.extraction.router._try_text_llm", new_callable=AsyncMock) as mock_text, \
             patch("backend.extraction.router._try_vision_llm", new_callable=AsyncMock) as mock_vision, \
             patch("backend.extraction.router._run_local") as mock_local:
            mock_text.return_value = llm_result

            from backend.extraction.router import extract_items_async
            result = await extract_items_async(xlsx_parse_result)

            mock_text.assert_awaited_once()
            # Verify is_xlsx=True was passed as keyword arg
            call_kwargs = mock_text.call_args
            assert call_kwargs[1].get("is_xlsx") is True

    @patch("backend.utils.load_config")
    async def test_fallback_text_to_vision(self, mock_load_config, text_pdf_parse_result, vision_result):
        """When Text LLM fails, should fall back to Vision LLM."""
        mock_load_config.return_value = {"extraction_enabled": True}

        with patch("backend.extraction.router._try_text_llm", new_callable=AsyncMock) as mock_text, \
             patch("backend.extraction.router._try_vision_llm", new_callable=AsyncMock) as mock_vision, \
             patch("backend.extraction.router._run_local") as mock_local:
            mock_text.return_value = None  # Text LLM failed
            mock_vision.return_value = vision_result

            from backend.extraction.router import extract_items_async
            result = await extract_items_async(text_pdf_parse_result)

            mock_text.assert_awaited_once()
            mock_vision.assert_awaited_once()
            mock_local.assert_not_called()
            assert result.extraction_method == "vision"

    @patch("backend.utils.load_config")
    async def test_fallback_both_ai_to_local(self, mock_load_config, text_pdf_parse_result, local_result):
        """When both AI methods fail, should fall back to local."""
        mock_load_config.return_value = {"extraction_enabled": True}

        with patch("backend.extraction.router._try_text_llm", new_callable=AsyncMock) as mock_text, \
             patch("backend.extraction.router._try_vision_llm", new_callable=AsyncMock) as mock_vision, \
             patch("backend.extraction.router._run_local") as mock_local:
            mock_text.return_value = None  # Text LLM failed
            mock_vision.return_value = None  # Vision LLM failed
            mock_local.return_value = local_result

            from backend.extraction.router import extract_items_async
            result = await extract_items_async(text_pdf_parse_result)

            mock_text.assert_awaited_once()
            mock_vision.assert_awaited_once()
            mock_local.assert_called_once()
            assert result.extraction_method == "local"

    @patch("backend.utils.load_config")
    async def test_extraction_disabled_uses_local(self, mock_load_config, text_pdf_parse_result, local_result):
        """When extraction_enabled=False, should use local extraction directly."""
        mock_load_config.return_value = {"extraction_enabled": False}

        with patch("backend.extraction.router._try_text_llm", new_callable=AsyncMock) as mock_text, \
             patch("backend.extraction.router._try_vision_llm", new_callable=AsyncMock) as mock_vision, \
             patch("backend.extraction.router._run_local") as mock_local:
            mock_local.return_value = local_result

            from backend.extraction.router import extract_items_async
            result = await extract_items_async(text_pdf_parse_result)

            mock_text.assert_not_awaited()
            mock_vision.assert_not_awaited()
            mock_local.assert_called_once()
            assert result.extraction_method == "local"

    @patch("backend.utils.load_config")
    async def test_no_pdf_path_skips_vision(self, mock_load_config, scanned_pdf_parse_result, local_result):
        """When pdf_path is missing, should skip Vision LLM and fall back to local."""
        mock_load_config.return_value = {"extraction_enabled": True}
        scanned_pdf_parse_result["pdf_path"] = ""  # No pdf_path

        with patch("backend.extraction.router._try_text_llm", new_callable=AsyncMock) as mock_text, \
             patch("backend.extraction.router._try_vision_llm", new_callable=AsyncMock) as mock_vision, \
             patch("backend.extraction.router._run_local") as mock_local:
            mock_text.return_value = None
            mock_vision.return_value = None  # Vision returns None for empty pdf_path
            mock_local.return_value = local_result

            from backend.extraction.router import extract_items_async
            result = await extract_items_async(scanned_pdf_parse_result)

            mock_text.assert_not_awaited()  # No text content → skip Text LLM
            # Router passes empty pdf_path to _try_vision_llm; the real function
            # returns None immediately, and our mock also returns None.
            mock_vision.assert_awaited_once_with("", mock_load_config.return_value)
            mock_local.assert_called_once()
            assert result.extraction_method == "local"


class TestRouterHelpers:
    """Test the internal router helper functions directly."""

    def test_get_pages_text(self):
        """_get_pages_text extracts text from pdfplumber pages."""
        from backend.extraction.router import _get_pages_text

        parse_result = {
            "parsers": {
                "pdfplumber": {
                    "pages": [
                        {"page": 1, "text": "Page 1 content"},
                        {"page": 2, "text": "Page 2 content"},
                    ],
                }
            },
        }
        texts = _get_pages_text(parse_result)
        assert texts == ["Page 1 content", "Page 2 content"]

    def test_get_pages_text_empty(self):
        """_get_pages_text returns empty list when no pages."""
        from backend.extraction.router import _get_pages_text
        assert _get_pages_text({}) == []

    def test_get_pages_text_missing_parser(self):
        """_get_pages_text handles missing pdfplumber parser."""
        from backend.extraction.router import _get_pages_text
        assert _get_pages_text({"parsers": {}}) == []

    def test_run_local_creates_result(self):
        """_run_local wraps local_extract result in ExtractionResult."""
        from backend.extraction.router import _run_local, ExtractionResult

        parse_result = {
            "parsers": {
                "pdfplumber": {
                    "pages": [{
                        "page": 1,
                        "tables": [{
                            "rows": [
                                ["Item", "Brand", "Model", "Qty", "Unit Price"],
                                ["1", "BOSCH", "X1", "10", "25.00"],
                            ],
                            "strategy": "text",
                        }],
                    }],
                }
            },
        }
        result = _run_local(parse_result)
        assert isinstance(result, ExtractionResult)
        assert result.extraction_method == "local"
        assert len(result.items) == 1
        assert result.items[0]["brand"] == "BOSCH"


# =============================================================================
# ExtractionResult dataclass tests
# =============================================================================


class TestExtractionResult:
    """Test the ExtractionResult dataclass."""

    def test_default_values(self):
        """ExtractionResult should have sensible defaults."""
        from backend.extraction.router import ExtractionResult
        result = ExtractionResult()
        assert result.items == []
        assert result.supplier == ""
        assert result.date == ""
        assert result.currency == ""
        assert result.document_type == "unknown"
        assert result.extraction_method == "local"
        assert result.warnings == []

    def test_with_data(self):
        """ExtractionResult should store provided data."""
        from backend.extraction.router import ExtractionResult
        result = ExtractionResult(
            items=[{"brand": "BOSCH"}],
            supplier="ABC Corp",
            date="2026-06-15",
            currency="HKD",
            document_type="QUO",
            extraction_method="llm",
            warnings=["Test warning"],
        )
        assert result.items == [{"brand": "BOSCH"}]
        assert result.supplier == "ABC Corp"
        assert result.date == "2026-06-15"
        assert result.currency == "HKD"
        assert result.document_type == "QUO"
        assert result.extraction_method == "llm"
        assert result.warnings == ["Test warning"]


# =============================================================================
# LLM module tests — mock httpx to test response parsing and error handling
# =============================================================================


class TestLlmCallLlm:
    """Test the _call_llm function in llm.py."""

    @pytest.fixture
    def cfg(self):
        return {
            "ai_endpoint": "http://test:1234/v1/chat/completions",
            "model": "test-model",
            "timeout": 30,
            "max_retries": 2,
        }

    @pytest.fixture
    def sample_text(self):
        return "Brand | Model | Qty | Unit Price\nBOSCH | X1 | 10 | 25.00\n"

    # ── Helper to set up httpx mock ──────────────────────────────────

    def _setup_mock_client(self, mock_async_client, mock_response):
        """Configure a mocked httpx.AsyncClient to return mock_response on post()."""
        mock_async_client.return_value.__aenter__.return_value.post.return_value = mock_response

    def _setup_mock_client_side_effect(self, mock_async_client, side_effect_fn):
        """Configure a mocked httpx.AsyncClient to call side_effect_fn on post()."""
        mock_async_client.return_value.__aenter__.return_value.post = side_effect_fn

    # ── Success cases ────────────────────────────────────────────────

    async def test_successful_call(self, cfg, sample_text):
        """_call_llm should parse a valid JSON response."""
        from backend.extraction.llm import _call_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": (
                        '{\n'
                        '  "document_type": "QUO",\n'
                        '  "supplier": "ABC Corp",\n'
                        '  "date": "15-Jun-2026",\n'
                        '  "currency": "HKD",\n'
                        '  "items": [\n'
                        '    {"brand": "BOSCH", "model": "X1", "description": "Sensor",\n'
                        '     "quantity": "10", "unit": "pc", "unit_price": "25.00", "total": ""}\n'
                        '  ]\n'
                        '}'
                    ),
                }
            }],
        }

        with patch("httpx.AsyncClient") as mock_client:
            self._setup_mock_client(mock_client, mock_response)
            result = await _call_llm(sample_text, cfg, is_xlsx=False)

        assert result["document_type"] == "QUO"
        assert result["supplier"] == "ABC Corp"
        assert result["currency"] == "HKD"
        assert result["date"] == "2026-06-15"  # normalized by normalize_date
        assert len(result["items"]) == 1
        assert result["items"][0]["brand"] == "BOSCH"
        assert result["items"][0]["model"] == "X1"
        assert result["items"][0]["unit_price"] == "25.00"
        assert result["llm_warnings"] == []

    async def test_markdown_code_blocks(self, cfg, sample_text):
        """_call_llm should strip ```json ... ``` wrappers."""
        from backend.extraction.llm import _call_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": (
                        '```json\n'
                        '{"document_type": "QUO", "supplier": "ABC", "date": "",'
                        ' "currency": "HKD", "items": []}\n'
                        '```'
                    ),
                }
            }],
        }

        with patch("httpx.AsyncClient") as mock_client:
            self._setup_mock_client(mock_client, mock_response)
            result = await _call_llm(sample_text, cfg, is_xlsx=False)

        assert result["document_type"] == "QUO"
        assert result["supplier"] == "ABC"
        assert result["items"] == []

    # ── Error / retry cases ──────────────────────────────────────────

    async def test_retry_on_http_error(self, cfg, sample_text):
        """_call_llm should retry on HTTP 500 and return warnings after exhaustion."""
        from backend.extraction.llm import _call_llm

        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500
        mock_response_fail.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client:
            self._setup_mock_client(mock_client, mock_response_fail)
            result = await _call_llm(sample_text, cfg, is_xlsx=False)

        assert result["items"] == []
        assert len(result["llm_warnings"]) == 1
        assert "LLM extraction failed" in result["llm_warnings"][0]

    async def test_retry_on_connection_error(self, cfg, sample_text):
        """_call_llm should retry and return warnings on connection error."""
        from backend.extraction.llm import _call_llm

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.side_effect = \
                httpx.ConnectError("Connection refused")

            result = await _call_llm(sample_text, cfg, is_xlsx=False)

        assert result["items"] == []
        assert len(result["llm_warnings"]) == 1
        assert "Connection error" in result["llm_warnings"][0]

    async def test_no_json_in_response(self, cfg, sample_text):
        """_call_llm should handle responses with no JSON content."""
        from backend.extraction.llm import _call_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": "Sorry, I cannot process this document."},
            }],
        }

        with patch("httpx.AsyncClient") as mock_client:
            self._setup_mock_client(mock_client, mock_response)
            result = await _call_llm(sample_text, cfg, is_xlsx=False)

        assert result["items"] == []
        assert len(result["llm_warnings"]) == 1
        # After max_retries, should report the last error
        assert result["llm_warnings"][0] == "LLM extraction failed: No JSON in response"

    async def test_retry_on_timeout(self, cfg, sample_text):
        """_call_llm should retry on timeout and return warnings."""
        from backend.extraction.llm import _call_llm

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.side_effect = \
                httpx.TimeoutException("Request timed out")

            result = await _call_llm(sample_text, cfg, is_xlsx=False)

        assert result["items"] == []
        assert len(result["llm_warnings"]) == 1
        assert "Timeout" in result["llm_warnings"][0]

    async def test_returns_valid_data_after_retry(self, cfg, sample_text):
        """_call_llm should succeed on retry after initial failure."""
        from backend.extraction.llm import _call_llm

        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_fail.text = "Error"

        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"document_type": "QUO", "supplier": "ABC",'
                               ' "date": "", "currency": "HKD", "items": []}',
                }
            }],
        }

        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_fail
            return mock_ok

        with patch("httpx.AsyncClient") as mock_client:
            self._setup_mock_client_side_effect(mock_client, post_side_effect)
            result = await _call_llm(sample_text, cfg, is_xlsx=False)

        assert result["document_type"] == "QUO"
        assert result["supplier"] == "ABC"

    # ── Token limit tests ────────────────────────────────────────────

    async def test_xlsx_uses_more_tokens(self, cfg, sample_text):
        """_call_llm should use 8192 max_tokens for XLSX vs 4096 for PDF."""
        from backend.extraction.llm import _call_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"document_type": "QUO", "supplier": "ABC",'
                               ' "date": "", "currency": "HKD", "items": []}',
                }
            }],
        }

        posted_json = {}

        async def capture_post(url, **kwargs):
            posted_json.update(kwargs.get("json", {}))
            return mock_response

        with patch("httpx.AsyncClient") as mock_client:
            self._setup_mock_client_side_effect(mock_client, capture_post)
            result = await _call_llm(sample_text, cfg, is_xlsx=True)

        assert posted_json.get("max_tokens") == 8192
        assert result["document_type"] == "QUO"

    async def test_pdf_uses_4096_tokens(self, cfg, sample_text):
        """_call_llm should use 4096 max_tokens for PDF."""
        from backend.extraction.llm import _call_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"document_type": "QUO", "supplier": "ABC",'
                               ' "date": "", "currency": "HKD", "items": []}',
                }
            }],
        }

        posted_json = {}

        async def capture_post(url, **kwargs):
            posted_json.update(kwargs.get("json", {}))
            return mock_response

        with patch("httpx.AsyncClient") as mock_client:
            self._setup_mock_client_side_effect(mock_client, capture_post)
            result = await _call_llm(sample_text, cfg, is_xlsx=False)

        assert posted_json.get("max_tokens") == 4096
        assert result["document_type"] == "QUO"


class TestLlmNormalizePages:
    """Test normalize_pages_with_llm."""

    @pytest.fixture
    def cfg(self):
        return {
            "ai_endpoint": "http://test:1234/v1/chat/completions",
            "model": "test-model",
            "timeout": 30,
            "max_retries": 2,
        }

    @patch("backend.extraction.llm._call_llm", new_callable=AsyncMock)
    async def test_pdf_combines_all_pages(self, mock_call, cfg):
        """For PDFs, all non-empty pages should be combined into one LLM call."""
        from backend.extraction.llm import normalize_pages_with_llm

        mock_call.return_value = {
            "document_type": "QUO", "supplier": "ABC Corp",
            "currency": "HKD", "date": "2026-06-15",
            "items": [{"brand": "BOSCH"}], "llm_warnings": [],
        }

        pages = ["Page 1 content", "Page 2 content", "Page 3 content"]
        result = await normalize_pages_with_llm(pages, cfg, is_xlsx=False)

        assert result["supplier"] == "ABC Corp"
        assert len(result["items"]) == 1
        mock_call.assert_awaited_once()

    @patch("backend.extraction.llm._call_llm", new_callable=AsyncMock)
    async def test_pdf_skips_empty_pages(self, mock_call, cfg):
        """For PDFs, empty pages should be filtered out before combining."""
        from backend.extraction.llm import normalize_pages_with_llm

        mock_call.return_value = {
            "document_type": "unknown", "supplier": "",
            "currency": "", "date": "",
            "items": [], "llm_warnings": [],
        }

        pages = ["Page 1 content", "", "   "]
        result = await normalize_pages_with_llm(pages, cfg, is_xlsx=False)

        mock_call.assert_awaited_once()
        # The combined text should only contain the non-empty page
        call_text = mock_call.call_args[0][0]
        assert "Page 1 content" in call_text
        assert "Page 2" not in call_text

    @patch("backend.extraction.llm._call_llm", new_callable=AsyncMock)
    async def test_xlsx_processes_each_sheet_separately(self, mock_call, cfg):
        """For XLSX, each sheet should be a separate LLM call."""
        from backend.extraction.llm import normalize_pages_with_llm

        page1_result = {
            "document_type": "QUO", "supplier": "ABC Corp",
            "currency": "HKD", "date": "2026-06-15",
            "items": [{"brand": "BOSCH", "model": "X1"}], "llm_warnings": [],
        }
        page2_result = {
            "document_type": "unknown", "supplier": "",
            "currency": "HKD", "date": "",
            "items": [{"brand": "Siemens", "model": "Y2"}], "llm_warnings": [],
        }
        mock_call.side_effect = [page1_result, page2_result]

        sheets = ["Sheet 1 data", "Sheet 2 data"]
        result = await normalize_pages_with_llm(sheets, cfg, is_xlsx=True)

        assert mock_call.call_count == 2
        assert len(result["items"]) == 2
        assert result["supplier"] == "ABC Corp"  # From first sheet
        assert result["currency"] == "HKD"  # From first sheet

    @patch("backend.extraction.llm._call_llm", new_callable=AsyncMock)
    async def test_xlsx_uses_metadata_from_first_sheet(self, mock_call, cfg):
        """XLSX should use supplier/date/currency from the first sheet that provides it."""
        from backend.extraction.llm import normalize_pages_with_llm

        sheet1 = {
            "document_type": "unknown", "supplier": "",
            "currency": "", "date": "",
            "items": [{"brand": "BOSCH"}], "llm_warnings": [],
        }
        sheet2 = {
            "document_type": "QUO", "supplier": "ABC Corp",
            "currency": "HKD", "date": "2026-06-15",
            "items": [{"brand": "Siemens"}], "llm_warnings": [],
        }
        mock_call.side_effect = [sheet1, sheet2]

        sheets = ["Sheet 1", "Sheet 2"]
        result = await normalize_pages_with_llm(sheets, cfg, is_xlsx=True)

        assert result["supplier"] == "ABC Corp"
        assert result["document_type"] == "QUO"
        assert result["items"] == [{"brand": "BOSCH"}, {"brand": "Siemens"}]

    async def test_no_endpoint_config(self):
        """When endpoint is not configured, should return empty with warning."""
        from backend.extraction.llm import normalize_pages_with_llm

        result = await normalize_pages_with_llm(["some text"], {
            "ai_endpoint": "", "model": "",
        })

        assert result["items"] == []
        assert len(result["llm_warnings"]) == 1
        assert "not configured" in result["llm_warnings"][0]

    async def test_no_text_content(self, cfg):
        """When all pages are empty, should return empty with warning."""
        from backend.extraction.llm import normalize_pages_with_llm

        result = await normalize_pages_with_llm(["", "  ", ""], cfg)

        assert result["items"] == []
        assert len(result["llm_warnings"]) == 1
        assert "No text content" in result["llm_warnings"][0]


class TestLlmCleanItem:
    """Test the _clean_item helper in llm.py."""

    def test_clean_item_strips_whitespace(self):
        """_clean_item should strip whitespace from all fields."""
        from backend.extraction.llm import _clean_item
        item = _clean_item({
            "brand": "  BOSCH  ",
            "model": " X1 ",
            "description": "  Sensor  ",
            "quantity": " 10 ",
            "unit": " pc ",
            "unit_price": " 25.00 ",
            "total": " 250.00 ",
        })
        assert item["brand"] == "BOSCH"
        assert item["model"] == "X1"
        assert item["description"] == "Sensor"
        assert item["quantity"] == "10"
        assert item["unit"] == "pc"
        assert item["unit_price"] == "25.00"
        assert item["total"] == "250.00"

    def test_clean_item_filters_empty(self):
        """_clean_item should return None for items with no meaningful content."""
        from backend.extraction.llm import _clean_item
        assert _clean_item(None) is None
        assert _clean_item({}) is None
        assert _clean_item({"brand": "", "model": "", "description": "",
                            "unit_price": ""}) is None

    def test_clean_item_keeps_valid_empty_brand(self):
        """_clean_item should keep items that have model or description even without brand."""
        from backend.extraction.llm import _clean_item
        item = _clean_item({
            "brand": "",
            "model": "X1",
            "description": "",
            "quantity": "",
            "unit": "",
            "unit_price": "",
            "total": "",
        })
        assert item is not None
        assert item["model"] == "X1"
        assert item["brand"] == ""

    def test_clean_item_handles_none_values(self):
        """_clean_item should handle None values gracefully."""
        from backend.extraction.llm import _clean_item
        item = _clean_item({
            "brand": None,
            "model": "X1",
            "description": None,
            "quantity": None,
            "unit": None,
            "unit_price": None,
            "total": None,
        })
        assert item is not None
        assert item["brand"] == ""
        assert item["model"] == "X1"


# =============================================================================
# Vision module tests — response parsing and cleanup
# =============================================================================


class TestVisionCleanItem:
    """Test the _clean_item helper in vision.py."""

    def test_clean_item_standard_keys(self):
        """vision.py's _clean_item only keeps known keys (brand, model, etc)."""
        from backend.extraction.vision import _clean_item
        item = _clean_item({
            "brand": "BOSCH",
            "model": "X1",
            "description": "Sensor",
            "quantity": "10",
            "unit": "pc",
            "unit_price": "25.00",
            "total": "250.00",
            "remark": "",
        })
        assert item is not None
        assert item["brand"] == "BOSCH"
        assert item["model"] == "X1"
        assert item["unit_price"] == "25.00"
        # Note: 'page' is stripped by _clean_item (not in its allowed keys)

    def test_clean_item_filters_empty(self):
        """vision.py's _clean_item should filter empty items."""
        from backend.extraction.vision import _clean_item
        assert _clean_item(None) is None
        assert _clean_item({"model": "", "description": "", "unit_price": ""}) is None

    def test_clean_item_keeps_valid_no_description(self):
        """vision's _clean_item keeps items with model+price even without description."""
        from backend.extraction.vision import _clean_item
        item = _clean_item({
            "brand": "", "model": "X1", "description": "",
            "quantity": "", "unit": "", "unit_price": "25.00", "total": "",
        })
        assert item is not None
        assert item["model"] == "X1"
        assert item["unit_price"] == "25.00"


# =============================================================================
# Integration-style tests with mocked httpx responses
# =============================================================================


class TestLlmIntegration:
    """End-to-end tests of llm module with mocked httpx."""

    @pytest.fixture
    def cfg(self):
        return {
            "ai_endpoint": "http://test:1234/v1/chat/completions",
            "model": "test-model",
            "timeout": 30,
            "max_retries": 2,
        }

    async def test_normalize_pages_with_multiple_items(self, cfg):
        """Full flow: normalize_pages_with_llm with mocked httpx returns proper items."""
        from backend.extraction.llm import normalize_pages_with_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": (
                        '{\n'
                        '  "document_type": "QUO",\n'
                        '  "supplier": "ABC Corp Ltd",\n'
                        '  "date": "15-Jun-2026",\n'
                        '  "currency": "HKD",\n'
                        '  "items": [\n'
                        '    {"brand": "BOSCH", "model": "X1", "description": "Sensor",'
                        ' "quantity": "10", "unit": "pc", "unit_price": "25.00", "total": "250.00"},\n'
                        '    {"brand": "Siemens", "model": "Y2", "description": "Probe",'
                        ' "quantity": "5", "unit": "pc", "unit_price": "50.00", "total": "250.00"}\n'
                        '  ]\n'
                        '}'
                    ),
                }
            }],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

            result = await normalize_pages_with_llm(
                ["Brand | Model | Qty | Price\nBOSCH | X1 | 10 | 25.00\n"],
                cfg, is_xlsx=False,
            )

        assert result["document_type"] == "QUO"
        assert result["supplier"] == "ABC Corp Ltd"
        assert result["currency"] == "HKD"
        assert len(result["items"]) == 2
        assert result["items"][0]["brand"] == "BOSCH"
        assert result["items"][1]["model"] == "Y2"

    async def test_clean_item_filters_bad_items_in_response(self, cfg):
        """Items that fail _clean_item should not appear in the result."""
        from backend.extraction.llm import normalize_pages_with_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": (
                        '{"document_type": "unknown", "supplier": "",'
                        ' "date": "", "currency": "",'
                        ' "items": ['
                        '   {"brand": "BOSCH", "model": "X1", "description": "OK",'
                        '    "quantity": "10", "unit": "pc", "unit_price": "25.00", "total": ""},'
                        '   {"brand": "", "model": "", "description": "",'
                        '    "quantity": "", "unit": "", "unit_price": "", "total": ""}'
                        ']}'
                    ),
                }
            }],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

            result = await normalize_pages_with_llm(
                ["test data"], cfg, is_xlsx=False,
            )

        # Only the valid item should be present
        assert len(result["items"]) == 1
        assert result["items"][0]["brand"] == "BOSCH"


# =============================================================================
# Router integration with real internal functions (no HTTP)
# =============================================================================


class TestRouterIntegration:
    """Test the router's integration with llm.py and local.py.

    These tests verify that when _try_text_llm calls the real llm module,
    the ExtractionResult is correctly constructed.
    """

    @patch("backend.extraction.router.normalize_pages_with_llm", new_callable=AsyncMock)
    async def test_try_text_llm_creates_result(self, mock_norm):
        """_try_text_llm should create an ExtractionResult from LLM output."""
        from backend.extraction.router import _try_text_llm

        mock_norm.return_value = {
            "document_type": "QUO",
            "supplier": "ABC Corp",
            "date": "2026-06-15",
            "currency": "HKD",
            "items": [{"brand": "BOSCH", "model": "X1"}],
            "llm_warnings": [],
        }

        result = await _try_text_llm(["some text"], {"ai_endpoint": "x", "model": "y"})

        assert result is not None
        assert result.extraction_method == "llm"
        assert result.supplier == "ABC Corp"
        assert result.date == "2026-06-15"
        assert len(result.items) == 1

    @patch("backend.extraction.router.normalize_pages_with_llm", new_callable=AsyncMock)
    async def test_try_text_llm_returns_none_on_no_items(self, mock_norm):
        """_try_text_llm should return None if LLM returns no items."""
        from backend.extraction.router import _try_text_llm

        mock_norm.return_value = {
            "document_type": "unknown", "supplier": "", "date": "",
            "currency": "", "items": [], "llm_warnings": [],
        }

        result = await _try_text_llm(["some text"], {"ai_endpoint": "x", "model": "y"})
        assert result is None

    @patch("backend.extraction.router.normalize_pages_with_llm", new_callable=AsyncMock)
    async def test_try_text_llm_returns_none_on_empty_text(self, mock_norm):
        """_try_text_llm should return None if all pages are empty."""
        from backend.extraction.router import _try_text_llm

        result = await _try_text_llm(["", ""], {"ai_endpoint": "x", "model": "y"})
        assert result is None
        mock_norm.assert_not_awaited()

    @patch("backend.extraction.router.extract_with_vision", new_callable=AsyncMock)
    async def test_try_vision_llm_creates_result(self, mock_vision):
        """_try_vision_llm should create an ExtractionResult from vision output."""
        from backend.extraction.router import _try_vision_llm

        mock_vision.return_value = {
            "document_type": "QUO",
            "supplier": "ABC Corp",
            "date": "2026-06-15",
            "currency": "HKD",
            "items": [{"brand": "BOSCH", "model": "X1"}],
            "warnings": [],
            "extraction_method": "vision",
        }

        result = await _try_vision_llm("/tmp/test.pdf", {"ai_endpoint": "x", "model": "y"})

        assert result is not None
        assert result.extraction_method == "vision"
        assert result.supplier == "ABC Corp"
        assert len(result.items) == 1

    @patch("backend.extraction.router.extract_with_vision", new_callable=AsyncMock)
    async def test_try_vision_llm_returns_none_on_no_pdf_path(self, mock_vision):
        """_try_vision_llm should return None if pdf_path is empty."""
        from backend.extraction.router import _try_vision_llm

        result = await _try_vision_llm("", {"ai_endpoint": "x", "model": "y"})
        assert result is None
        mock_vision.assert_not_awaited()
