"""Unit tests for the Python timetable helpers (mocked HTTP, no API calls)."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from bromcom import BromcomClient
from bromcom.exceptions import BromcomScopeError
from bromcom_helpers import TimetableHelper
from bromcom_helpers.timetables import _format_time, _monday_of, _next_weekday


class TestTimetables:
    def test_format_time(self):
        assert _format_time("1900-01-02T09:09:00") == "09:09"
        assert _format_time("2026-04-07T14:30:00") == "14:30"
        assert _format_time(None) == ""
        assert _format_time("") == ""

    def test_next_weekday_on_weekend(self):
        # Saturday -> Monday
        assert _next_weekday(date(2026, 4, 4)).weekday() == 0
        # Sunday -> Monday
        assert _next_weekday(date(2026, 4, 5)).weekday() == 0

    def test_next_weekday_on_weekday(self):
        wed = date(2026, 4, 8)
        assert _next_weekday(wed) == wed

    def test_monday_of(self):
        # Wednesday April 8 -> Monday April 6
        assert _monday_of(date(2026, 4, 8)) == date(2026, 4, 6)
        # Monday stays Monday
        assert _monday_of(date(2026, 4, 6)) == date(2026, 4, 6)

    def test_template_scope_error(self):
        http = MagicMock()
        http.get.side_effect = BromcomScopeError(403, "Forbidden")
        helper = TimetableHelper(http)
        with pytest.raises(BromcomScopeError, match="PeriodStructures"):
            helper.get_template(person_id=100, from_date="2026-04-07")

    def test_live_scope_error(self):
        http = MagicMock()
        http.get.side_effect = BromcomScopeError(403, "Forbidden")
        helper = TimetableHelper(http)
        with pytest.raises(BromcomScopeError, match="PeriodStructures"):
            helper.get_live(student_id=100, from_date="2026-04-07")

    def test_live_requires_student_or_staff(self):
        http = MagicMock()
        helper = TimetableHelper(http)
        with pytest.raises(ValueError, match="student_id or staff_id"):
            helper.get_live()

    def test_helper_accepts_client(self):
        """TimetableHelper(client) duck-types to client._http."""
        client = BromcomClient(app_id="id", app_secret="secret")
        helper = TimetableHelper(client)
        assert helper._http is client._http
