"""Integration tests for the timetable helpers against the Bromcom sandbox.

These tests require BROMCOM_APP_ID, BROMCOM_APP_SECRET, and BROMCOM_SCHOOL_ID
environment variables. They are skipped if credentials are not available.

Run with: uv run pytest python-helpers/tests/test_sandbox_timetables.py -v
"""

import os

import pytest

from bromcom import BromcomClient
from bromcom_helpers import TimetableHelper

APP_ID = os.environ.get("BROMCOM_APP_ID")
APP_SECRET = os.environ.get("BROMCOM_APP_SECRET")
_SCHOOL_ID = os.environ.get("BROMCOM_SCHOOL_ID")
SCHOOL_ID = int(_SCHOOL_ID) if _SCHOOL_ID else None

skip_no_creds = pytest.mark.skipif(
    not APP_ID or not APP_SECRET or not SCHOOL_ID,
    reason="BROMCOM_APP_ID, BROMCOM_APP_SECRET, and BROMCOM_SCHOOL_ID not set",
)


@pytest.fixture
def client():
    with BromcomClient(app_id=APP_ID, app_secret=APP_SECRET, school_id=SCHOOL_ID) as c:
        yield c


@pytest.fixture
def timetables(client):
    return TimetableHelper(client)


@skip_no_creds
class TestSandboxTimetableHelper:
    def test_template_timetable(self, client, timetables):
        """Find a collection with timetable data and verify template structure."""
        # Sandbox data is historical — find a CollectionTimetable and use its dates
        ct_list = client.collection.get_collection_timetables()
        if not ct_list:
            pytest.skip("No CollectionTimetables in sandbox")
        template = None
        for ct in ct_list[:10]:
            members = client.collection.get_collection_associates(
                entity_filter=f"collectionID={ct.collection_id}"
            )
            if not members:
                continue
            from_date = ct.start_date[:10] if ct.start_date else "2013-09-01"
            blocks = timetables.get_template(
                person_id=members[0].person_id, from_date=from_date
            )
            if blocks:
                template = blocks
                break
        if template is None:
            pytest.skip("No template timetable data found in sandbox")
        block = template[0]
        assert block.valid_from
        assert block.valid_to
        assert len(block.timetable) > 0
        first_week = list(block.timetable.values())[0]
        first_day = list(first_week.values())[0]
        assert len(first_day) > 0
        slot = first_day[0]
        assert hasattr(slot, "period")
        assert hasattr(slot, "start_time")

    def test_live_timetable_staff(self, client, timetables):
        """Try multiple staff until one returns a non-empty live timetable."""
        staff_list = client.staff.get_staff()
        assert len(staff_list) > 0
        timetable = None
        for s in staff_list[:20]:
            result = timetables.get_live(staff_id=s.staff_id)
            if result:
                timetable = result
                break
        if timetable is None:
            pytest.skip("No staff in sandbox have live timetable data")
        first_week = list(timetable.values())[0]
        first_day = list(first_week.values())[0]
        assert len(first_day) > 0
