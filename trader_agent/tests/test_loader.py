"""Tests for loader.py."""

from unittest.mock import patch

from trader_agent.tools.loader import load_screener, load_fmv_history
from ms_screener.src.config import DEFAULT_SNAPSHOT_TAB, DEFAULT_FMV_HISTORY_TAB


FAKE_ROWS = [{"ticker": "AAPL", "price": "150"}]


class TestLoadScreener:
    def test_calls_correct_tab(self):
        with patch("trader_agent.tools.loader.read_sheet_as_dicts", return_value=FAKE_ROWS) as mock:
            result = load_screener(sheet_id="fake-id")
        mock.assert_called_once_with("fake-id", DEFAULT_SNAPSHOT_TAB)
        assert result == FAKE_ROWS

    def test_custom_tab(self):
        with patch("trader_agent.tools.loader.read_sheet_as_dicts", return_value=FAKE_ROWS) as mock:
            load_screener(sheet_id="fake-id", tab="OtherTab")
        mock.assert_called_once_with("fake-id", "OtherTab")


class TestLoadFmvHistory:
    """Data_Changes goes through the layout-aware reader, not the header-driven one."""

    def test_calls_correct_tab(self):
        with patch("trader_agent.tools.loader.read_data_change_rows", return_value=FAKE_ROWS) as mock:
            result = load_fmv_history(sheet_id="fake-id")
        mock.assert_called_once_with("fake-id", DEFAULT_FMV_HISTORY_TAB)
        assert result == FAKE_ROWS

    def test_custom_tab(self):
        with patch("trader_agent.tools.loader.read_data_change_rows", return_value=FAKE_ROWS) as mock:
            load_fmv_history(sheet_id="fake-id", tab="CustomTab")
        mock.assert_called_once_with("fake-id", "CustomTab")
