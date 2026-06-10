"""Unit tests for the Python timetable helpers (mocked HTTP, no API calls)."""

from datetime import date
from types import SimpleNamespace
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


def _live_entry(**overrides):
    base = dict(
        student_id=1,
        day_of_week="Monday",
        week_display_name="Week 1",
        period_display_name="P1",
        period_start_date="2026-06-08",
        period_start_time="1900-01-01T09:10:00",
        period_end_time="1900-01-01T10:10:00",
        class_name="10X/Ma1",
        staff_id=42,
        is_cover=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_helper(timetable_batches):
    """Helper backed by a stub transport serving canned getLive responses."""
    state = {"batch": 0}

    def fake_get(path, **kwargs):
        if path == "/v2/PeriodStructures":
            return [
                SimpleNamespace(calendar_type_name="PERIOD", week_number=1),
                SimpleNamespace(calendar_type_name="PERIOD", week_number=2),
            ]
        if path == "/v2/StudentTimetables":
            batch = state["batch"]
            state["batch"] += 1
            return timetable_batches[batch] if batch < len(timetable_batches) else []
        if path == "/v2/Staff":
            return [SimpleNamespace(staff_id=42, staff_code="NW")]
        raise AssertionError(f"unexpected path {path}")

    http = MagicMock()
    http.get.side_effect = fake_get
    return TimetableHelper(http)


class TestGetLiveMocked:
    def test_assembles_slots(self):
        helper = _make_helper([[_live_entry(class_name="10X/Ma1\r\nExtra")]])
        result = helper.get_live(student_id=1, from_date="2026-06-08")
        assert list(result.keys()) == ["Week 1"]
        assert list(result["Week 1"].keys()) == ["Monday"]
        slot = result["Week 1"]["Monday"][0]
        assert slot.period == "P1"
        assert slot.start_time == "09:10"
        assert slot.end_time == "10:10"
        assert slot.class_name == "10X/Ma1 Extra"
        assert slot.room is None
        assert slot.staff_code == "NW"
        assert slot.teacher_id == 42
        assert slot.is_cover is False

    def test_dedup_keeps_most_recent(self):
        helper = _make_helper([[
            _live_entry(period_start_date="2026-06-01", class_name="OLD"),
            _live_entry(period_start_date="2026-06-08", class_name="NEW"),
        ]])
        result = helper.get_live(student_id=1, from_date="2026-06-08")
        slots = result["Week 1"]["Monday"]
        assert len(slots) == 1
        assert slots[0].class_name == "NEW"

    def test_dedup_treats_missing_period_as_one_key(self):
        helper = _make_helper([[
            _live_entry(period_display_name=None, period_start_date="2026-06-01", class_name="OLD"),
            _live_entry(period_display_name=None, period_start_date="2026-06-08", class_name="NEW"),
        ]])
        result = helper.get_live(student_id=1, from_date="2026-06-08")
        slots = result["Week 1"]["Monday"]
        assert len(slots) == 1
        assert slots[0].class_name == "NEW"
        assert slots[0].period == ""
