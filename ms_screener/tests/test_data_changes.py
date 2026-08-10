"""Tests for the Data_Changes column mapping (writer order, header repair, legacy rows)."""

from unittest.mock import MagicMock, patch

import pytest

from ms_screener.src import io_layer, transform
from ms_screener.src.transform import (
    FMV_CHANGE_HEADERS,
    LEGACY_FMV_CHANGE_HEADERS,
    detect_fmv_changes,
    map_data_change_row,
    read_data_change_rows,
)


class TestChangeRowColumnMapping:
    """detect_fmv_changes must emit exactly FMV_CHANGE_HEADERS keys, in that order."""

    @staticmethod
    def _rows():
        prev = [{
            "ticker": "MSFT", "company": "Microsoft Corp", "fair_value": "600",
            "stars": "5", "uncertainty": "Medium", "moat": "Wide",
            "ratings_date": "Apr 30, 2026",
        }]
        curr = [{
            "ticker": "MSFT", "company": "Microsoft Corp", "fair_value": "620",
            "stars": "4", "uncertainty": "Medium", "moat": "Wide",
            "ratings_date": "Aug 06, 2026",
        }]
        return prev, curr

    def test_keys_match_headers_exactly(self):
        prev, curr = self._rows()
        changes = detect_fmv_changes(prev, curr)
        assert len(changes) == 1
        assert list(changes[0].keys()) == FMV_CHANGE_HEADERS

    def test_date_columns_hold_dates_not_uncertainty(self):
        prev, curr = self._rows()
        row = detect_fmv_changes(prev, curr)[0]
        assert row["previous_rating_date"] == "Apr 30, 2026"
        assert row["current_rating_date"] not in ("Medium", "High", "Very High")
        assert row["previous_uncertainty"] == "Medium"
        assert row["current_uncertainty"] == "Medium"
        assert row["previous_moat"] == "Wide"
        assert row["current_moat"] == "Wide"

    def test_positional_write_lands_in_expected_cells(self):
        """The order values are serialized in must match the header list positionally."""
        prev, curr = self._rows()
        row = detect_fmv_changes(prev, curr)[0]
        cells = [io_layer._format_sheet_value(row.get(c)) for c in FMV_CHANGE_HEADERS]
        assert cells[8] == "Medium"          # previous_uncertainty
        assert cells[10] == "Wide"           # previous_moat
        assert cells[12] == "Apr 30, 2026"   # previous_rating_date
        assert len(cells) == 14

    def test_legacy_header_is_a_subsequence_of_current(self):
        """The historical breakage: columns were inserted mid-list, not appended."""
        assert io_layer.is_header_subsequence(LEGACY_FMV_CHANGE_HEADERS, FMV_CHANGE_HEADERS)
        assert LEGACY_FMV_CHANGE_HEADERS[8:] == ["previous_rating_date", "current_rating_date"]
        assert FMV_CHANGE_HEADERS[8:10] == ["previous_uncertainty", "current_uncertainty"]


class TestHeaderSubsequence:
    def test_identical(self):
        assert io_layer.is_header_subsequence(["a", "b"], ["a", "b"])

    def test_inserted_middle_column(self):
        assert io_layer.is_header_subsequence(["a", "c"], ["a", "b", "c"])

    def test_reordered_is_not_repairable(self):
        assert not io_layer.is_header_subsequence(["b", "a"], ["a", "b", "c"])

    def test_renamed_is_not_repairable(self):
        assert not io_layer.is_header_subsequence(["a", "x"], ["a", "b", "c"])

    def test_empty_existing(self):
        assert not io_layer.is_header_subsequence([], ["a"])


class TestAppendHeaderRepair:
    ROW = {header: header for header in FMV_CHANGE_HEADERS}

    def _append(self, existing_header, monkeypatch):
        service = MagicMock()
        with patch.object(io_layer, "_get_sheets_service", return_value=service), \
             patch.object(io_layer, "read_sheet_as_dicts", return_value=[{"date": "2026-05-18"}]), \
             patch.object(io_layer, "read_sheet_header", return_value=existing_header):
            io_layer.append_to_sheet("sid", "Data_Changes", [self.ROW],
                                     headers=FMV_CHANGE_HEADERS)
        return service

    def test_stale_header_is_repaired_before_appending(self, monkeypatch):
        service = self._append(LEGACY_FMV_CHANGE_HEADERS, monkeypatch)
        update = service.spreadsheets.return_value.values.return_value.update
        update.assert_called_once()
        assert update.call_args.kwargs["body"]["values"] == [FMV_CHANGE_HEADERS]
        assert update.call_args.kwargs["range"] == "Data_Changes!A1"

    def test_matching_header_is_left_alone(self, monkeypatch):
        service = self._append(list(FMV_CHANGE_HEADERS), monkeypatch)
        service.spreadsheets.return_value.values.return_value.update.assert_not_called()

    def test_unrepairable_header_raises(self, monkeypatch):
        with pytest.raises(RuntimeError, match="cannot be repaired"):
            self._append(["date", "company", "ticker"], monkeypatch)

    def test_appended_values_follow_header_order(self, monkeypatch):
        service = self._append(list(FMV_CHANGE_HEADERS), monkeypatch)
        append = service.spreadsheets.return_value.values.return_value.append
        values = append.call_args.kwargs["body"]["values"]
        # ROW maps each header to itself, so each cell must equal its column name
        assert [str(cell) for cell in values[0]] == [str(h) for h in FMV_CHANGE_HEADERS]


class TestLayoutAwareRowMapping:
    """Real rows from the sheet: 10-cell legacy rows and 14-cell modern rows."""

    LEGACY_CELLS = ["2026-05-18", "AMAT", "Applied Materials Inc", "380", "470",
                    "90", "3", "3", "Feb 12, 2026", "May 18, 2026"]
    MODERN_CELLS = ["2026-08-06", "MSFT", "Microsoft Corp", "600", "600", "0",
                    "5", "4", "Medium", "Medium", "Wide", "Wide",
                    "Apr 30, 2026", "Aug 06, 2026"]

    def test_legacy_row_dates_land_in_date_columns(self):
        row = map_data_change_row(self.LEGACY_CELLS)
        assert row["previous_rating_date"] == "Feb 12, 2026"
        assert row["current_rating_date"] == "May 18, 2026"
        assert row["previous_uncertainty"] is None
        assert row["current_uncertainty"] is None
        assert row["previous_moat"] is None and row["current_moat"] is None

    def test_modern_row_maps_straight_through(self):
        row = map_data_change_row(self.MODERN_CELLS)
        assert row["previous_uncertainty"] == "Medium"
        assert row["previous_moat"] == "Wide"
        assert row["previous_rating_date"] == "Apr 30, 2026"
        assert row["current_rating_date"] == "Aug 06, 2026"

    def test_every_row_has_the_full_key_set(self):
        for cells in (self.LEGACY_CELLS, self.MODERN_CELLS, []):
            assert set(map_data_change_row(cells)) == set(FMV_CHANGE_HEADERS)

    def test_short_row_pads_with_none(self):
        row = map_data_change_row(["2026-05-18", "AMAT"])
        assert row["company"] is None
        assert row["current_rating_date"] is None

    def test_empty_cells_become_none(self):
        cells = list(self.MODERN_CELLS)
        cells[12] = ""
        assert map_data_change_row(cells)["previous_rating_date"] is None

    def test_uncertainty_never_lands_in_a_date_column(self):
        for cells in (self.LEGACY_CELLS, self.MODERN_CELLS):
            row = map_data_change_row(cells)
            for column in ("previous_rating_date", "current_rating_date"):
                assert row[column] not in ("Low", "Medium", "High", "Very High", "Extreme")


class TestReadDataChangeRows:
    def _read(self, grid):
        with patch.object(transform.io_layer, "read_sheet_values", return_value=grid):
            return read_data_change_rows("sid", "Data_Changes")

    def test_stale_header_row_does_not_corrupt_the_read(self):
        """Sheet header is still the 10-column legacy row while data rows are 14 wide."""
        grid = [list(LEGACY_FMV_CHANGE_HEADERS),
                TestLayoutAwareRowMapping.LEGACY_CELLS,
                TestLayoutAwareRowMapping.MODERN_CELLS]
        rows = self._read(grid)
        assert len(rows) == 2
        assert rows[0]["previous_rating_date"] == "Feb 12, 2026"
        assert rows[1]["previous_uncertainty"] == "Medium"
        assert rows[1]["previous_rating_date"] == "Apr 30, 2026"

    def test_repaired_header_row_reads_the_same(self):
        data = [TestLayoutAwareRowMapping.LEGACY_CELLS,
                TestLayoutAwareRowMapping.MODERN_CELLS]
        stale = self._read([list(LEGACY_FMV_CHANGE_HEADERS), *data])
        repaired = self._read([[str(h) for h in FMV_CHANGE_HEADERS], *data])
        assert stale == repaired

    def test_blank_rows_skipped(self):
        grid = [list(LEGACY_FMV_CHANGE_HEADERS), ["", "", ""],
                TestLayoutAwareRowMapping.MODERN_CELLS]
        assert len(self._read(grid)) == 1

    def test_empty_tab(self):
        assert self._read([]) == []
