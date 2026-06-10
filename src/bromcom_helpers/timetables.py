"""Timetable helpers for the Bromcom Partner Data API.

Provides two views of a person's timetable:

- Template: the repeating schedule from collection/period definitions
- Live: actual lessons with covers and room changes

These helpers live in the ``bromcom_helpers`` package, separate from the core
``bromcom`` client, because their assumptions (e.g. a Collections-driven
school) are opinionated rather than universal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from bromcom import AsyncBromcomClient, BromcomClient
from bromcom.exceptions import BromcomScopeError


def _resolve_http(client_or_http: Any) -> Any:
    """Unwrap a BromcomClient/AsyncBromcomClient to its http transport, or pass through."""
    if isinstance(client_or_http, (BromcomClient, AsyncBromcomClient)):
        return client_or_http._http
    return client_or_http


@dataclass
class Slot:
    """A single timetable slot."""
    period: str
    start_time: str
    end_time: str
    class_name: str | None = None
    room: str | None = None
    staff_code: str | None = None
    teacher_id: int | None = None
    is_cover: bool = False


@dataclass
class TimetableBlock:
    """A timetable valid for a specific date range."""
    valid_from: str
    valid_to: str
    timetable: dict[str, dict[str, list[Slot]]] = field(default_factory=dict)


DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_date(s: str) -> date:
    """Parse an ISO date/datetime string to a date."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def _format_time(s: str | None) -> str:
    """Extract HH:MM from a datetime string like '1900-01-02T09:09:00'."""
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return s or ""


def _next_weekday(d: date) -> date:
    """Advance to next Monday-Friday if on a weekend."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _monday_of(d: date) -> date:
    """Get the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _derive_period_name(p: Any) -> str:
    """Get period name from periodDisplayName, or derive from calendarModelName."""
    if p.period_display_name:
        return p.period_display_name
    # calendarModelName follows DAY_X_WEEK pattern e.g. 'MON_1_1' or 'MON_AM_1'
    name = getattr(p, "calendar_model_name", "") or ""
    parts = name.split("_")
    if len(parts) >= 2:
        mid = parts[1]
        if mid.isdigit():
            return mid
        if mid in ("AM", "PM"):
            return mid
    return name


def _filter_periods(periods: list) -> list:
    """Filter PeriodStructures to PERIOD type only."""
    return [p for p in periods if getattr(p, "calendar_type_name", "") in ("PERIOD", "SESSION")]


def _get_cycle_length(http: Any, school_id: int | None = None) -> int:
    """Determine timetable cycle length from PeriodStructures."""
    from bromcom.models.timetable import PeriodStructures

    periods = http.get("/v2/PeriodStructures", school_id=school_id, model=PeriodStructures)
    periods = _filter_periods(periods)
    week_numbers = {p.week_number for p in periods if p.week_number}
    return max(len(week_numbers), 1)


async def _get_cycle_length_async(http: Any, school_id: int | None = None) -> int:
    """Async version of _get_cycle_length."""
    from bromcom.models.timetable import PeriodStructures

    periods = await http.get("/v2/PeriodStructures", school_id=school_id, model=PeriodStructures)
    periods = _filter_periods(periods)
    week_numbers = {p.week_number for p in periods if p.week_number}
    return max(len(week_numbers), 1)


def _fetch_staff_codes(http: Any, staff_ids: set[int], school_id: int | None = None) -> dict[int, str]:
    """Fetch staff codes for a set of staff IDs. Returns {staffID: staffCode}."""
    from bromcom.models.staff import Staff

    if not staff_ids:
        return {}
    staff_list = http.get("/v2/Staff", school_id=school_id, model=Staff)
    return {s.staff_id: s.staff_code for s in staff_list if s.staff_id in staff_ids and s.staff_code}


async def _fetch_staff_codes_async(http: Any, staff_ids: set[int], school_id: int | None = None) -> dict[int, str]:
    from bromcom.models.staff import Staff

    if not staff_ids:
        return {}
    staff_list = await http.get("/v2/Staff", school_id=school_id, model=Staff)
    return {s.staff_id: s.staff_code for s in staff_list if s.staff_id in staff_ids and s.staff_code}


def _fetch_locations(http: Any, location_ids: set[int], school_id: int | None = None) -> dict[int, str]:
    """Fetch location names for a set of location IDs. Returns {locationID: roomName}."""
    from bromcom.models.admin import Locations

    if not location_ids:
        return {}
    locs = http.get("/v2/Locations", school_id=school_id, model=Locations)
    result = {}
    for loc in locs:
        if loc.location_id in location_ids:
            name = loc.room_name or loc.location_description or loc.short_code or ""
            if name and name.upper() != "DEFAULT":
                result[loc.location_id] = name
    return result


async def _fetch_locations_async(http: Any, location_ids: set[int], school_id: int | None = None) -> dict[int, str]:
    from bromcom.models.admin import Locations

    if not location_ids:
        return {}
    locs = await http.get("/v2/Locations", school_id=school_id, model=Locations)
    result = {}
    for loc in locs:
        if loc.location_id in location_ids:
            name = loc.room_name or loc.location_description or loc.short_code or ""
            if name and name.upper() != "DEFAULT":
                result[loc.location_id] = name
    return result


class TimetableHelper:
    """Synchronous timetable helper.

    Pass either a ``BromcomClient`` or its underlying ``HttpClient``::

        from bromcom import BromcomClient
        from bromcom_helpers import TimetableHelper

        client = BromcomClient(app_id="...", app_secret="...", school_id=20001)
        timetables = TimetableHelper(client)
    """

    def __init__(self, client_or_http: Any) -> None:
        self._http = _resolve_http(client_or_http)

    def get_student_template(self, student: Any, from_date: str | date | None = None) -> list[TimetableBlock]:
        """Get the template timetable for a student."""
        return self.get_template(person_id=student.person_id, from_date=from_date)

    def get_staff_template(self, staff: Any, from_date: str | date | None = None) -> list[TimetableBlock]:
        """Get the template timetable for a staff member (staffID = personID)."""
        return self.get_template(person_id=staff.staff_id, from_date=from_date)

    def get_template(
        self,
        person_id: int,
        from_date: str | date | None = None,
        school_id: int | None = None,
    ) -> list[TimetableBlock]:
        """Build a template timetable from collection definitions.

        Returns a list of TimetableBlock objects. If the person's collections
        change within the timetable window, multiple blocks are returned.
        """
        from bromcom.models.collection import CollectionAssociates, CollectionTimetables
        from bromcom.models.timetable import PeriodStructures

        if from_date is None:
            from_date = _next_weekday(date.today())
        elif isinstance(from_date, str):
            from_date = _parse_date(from_date)

        try:
            cycle_length = _get_cycle_length(self._http, school_id)
        except BromcomScopeError:
            raise BromcomScopeError(
                403,
                "Timetables require access to the PeriodStructures endpoint. "
                "Ensure your API credentials have the required scope.",
            ) from None
        window_end = from_date + timedelta(weeks=cycle_length)

        # Get all collection memberships for this person
        try:
            associates = self._http.get(
                "/v2/CollectionAssociates",
                entity_filter=f"personID={person_id}",
                school_id=school_id,
                model=CollectionAssociates,
            )
        except BromcomScopeError:
            raise BromcomScopeError(
                403,
                "Template timetables require access to the CollectionAssociates endpoint. "
                "Ensure your API credentials have the required scope.",
            ) from None

        # Filter to those active in our window
        active = []
        for a in associates:
            start = _parse_date(a.start_date) if a.start_date else date.min
            end = _parse_date(a.end_date) if a.end_date else date.max
            if start < window_end and end >= from_date:
                active.append(a)

        if not active:
            return []

        # Detect date boundaries where collection memberships change
        boundaries = {from_date, window_end}
        for a in active:
            start = _parse_date(a.start_date) if a.start_date else date.min
            end = _parse_date(a.end_date) if a.end_date else date.max
            if from_date < start < window_end:
                boundaries.add(start)
            if from_date < end < window_end:
                boundaries.add(end + timedelta(days=1))
        boundaries = sorted(boundaries)

        # Get all CollectionTimetables for the active collections
        coll_ids = list({a.collection_id for a in active})
        all_ct: list = []
        for coll_id in coll_ids:
            ct = self._http.get(
                "/v2/CollectionTimetables",
                entity_filter=f"collectionID={coll_id}",
                school_id=school_id,
                model=CollectionTimetables,
            )
            for t in ct:
                t._collection_id = coll_id  # track which collection
            all_ct.extend(ct)

        # Get PeriodStructures for all referenced calendar models (PERIOD type only)
        cal_model_ids = list({t.calendar_model_id for t in all_ct})
        period_map: dict[int, list] = {}
        for cal_id in cal_model_ids:
            ps = self._http.get(
                "/v2/PeriodStructures",
                entity_filter=f"calendarModelID={cal_id}",
                school_id=school_id,
                model=PeriodStructures,
            )
            period_map[cal_id] = _filter_periods(ps)

        # Build name map from CollectionAssociates
        coll_names = {a.collection_id: a.collection_name for a in active}

        # Resolve staff codes and location names
        staff_ids = {t.employee_id for t in all_ct if t.employee_id}
        location_ids = {t.location_id for t in all_ct if t.location_id}
        staff_codes = _fetch_staff_codes(self._http, staff_ids, school_id)
        location_names = _fetch_locations(self._http, location_ids, school_id)

        # Build timetable blocks for each boundary period
        blocks = []
        for i in range(len(boundaries) - 1):
            block_start = boundaries[i]
            block_end = boundaries[i + 1] - timedelta(days=1)

            # Which collections are active in this sub-window?
            block_colls = set()
            for a in active:
                a_start = _parse_date(a.start_date) if a.start_date else date.min
                a_end = _parse_date(a.end_date) if a.end_date else date.max
                if a_start <= block_end and a_end >= block_start:
                    block_colls.add(a.collection_id)

            # Build the timetable grid from period structures
            timetable: dict[str, dict[str, list[Slot]]] = {}
            for ct in all_ct:
                if ct._collection_id not in block_colls:
                    continue
                ct_start = _parse_date(ct.start_date) if ct.start_date else date.min
                ct_end = _parse_date(ct.end_date) if ct.end_date else date.max
                if ct_start > block_end or ct_end < block_start:
                    continue

                periods = period_map.get(ct.calendar_model_id, [])
                for p in periods:
                    week = p.week_display_name or f"Week {p.week_number or 1}"
                    day = p.day_of_week
                    if not day:
                        continue

                    if week not in timetable:
                        timetable[week] = {}
                    if day not in timetable[week]:
                        timetable[week][day] = []

                    timetable[week][day].append(Slot(
                        period=_derive_period_name(p),
                        start_time=_format_time(p.default_start_time),
                        end_time=_format_time(p.default_end_time),
                        class_name=coll_names.get(ct._collection_id),
                        room=location_names.get(ct.location_id) if ct.location_id else None,
                        staff_code=staff_codes.get(ct.employee_id) if ct.employee_id else None,
                        teacher_id=ct.employee_id,
                    ))

            # Deduplicate and sort slots within each day
            for week in timetable:
                for day in timetable[week]:
                    seen_slots: set[tuple] = set()
                    unique: list[Slot] = []
                    for s in timetable[week][day]:
                        key = (s.start_time, s.end_time, s.class_name)
                        if key not in seen_slots:
                            seen_slots.add(key)
                            unique.append(s)
                    timetable[week][day] = sorted(unique, key=lambda s: s.start_time)
                # Sort days
                timetable[week] = dict(
                    sorted(timetable[week].items(), key=lambda kv: DAY_ORDER.index(kv[0]) if kv[0] in DAY_ORDER else 99)
                )

            if timetable:
                blocks.append(TimetableBlock(
                    valid_from=block_start.isoformat(),
                    valid_to=block_end.isoformat(),
                    timetable=timetable,
                ))

        return blocks

    def get_live(
        self,
        student_id: int | None = None,
        staff_id: int | None = None,
        from_date: str | date | None = None,
        include_cover: bool = True,
        school_id: int | None = None,
    ) -> dict[str, dict[str, list[Slot]]]:
        """Get the live timetable for a student or staff member.

        Fetches week by week efficiently using date range filters.
        """
        if student_id is None and staff_id is None:
            raise ValueError("Either student_id or staff_id must be provided")

        if from_date is None:
            from_date = _next_weekday(date.today())
        elif isinstance(from_date, str):
            from_date = _parse_date(from_date)

        try:
            cycle_length = _get_cycle_length(self._http, school_id)
        except BromcomScopeError:
            raise BromcomScopeError(
                403,
                "Timetables require access to the PeriodStructures endpoint. "
                "Ensure your API credentials have the required scope.",
            ) from None

        # Fetch week by week
        all_entries = []
        monday = _monday_of(from_date)
        for week_idx in range(cycle_length):
            week_start = monday + timedelta(weeks=week_idx)
            week_end = week_start + timedelta(days=4)  # Friday
            date_filter = (
                f"periodStartDate >= '{week_start.isoformat()}' "
                f"and periodStartDate <= '{week_end.isoformat()}'"
            )

            if student_id is not None:
                base_filter = f"studentID={student_id} and {date_filter}"
                from bromcom.models.student import StudentTimetables
                try:
                    entries = self._http.get(
                        "/v2/StudentTimetables",
                        entity_filter=base_filter,
                        school_id=school_id,
                        model=StudentTimetables,
                    )
                except BromcomScopeError:
                    raise BromcomScopeError(
                        403,
                        "Live student timetables require access to the StudentTimetables endpoint. "
                        "Ensure your API credentials have the required scope.",
                    ) from None
            else:
                base_filter = f"staffID={staff_id} and {date_filter}"
                if not include_cover:
                    base_filter += " and isCover=0"
                from bromcom.models.timetable import TimeTable
                try:
                    entries = self._http.get(
                        "/v2/TimeTable",
                        entity_filter=base_filter,
                        school_id=school_id,
                        model=TimeTable,
                    )
                except BromcomScopeError:
                    raise BromcomScopeError(
                        403,
                        "Live staff timetables require access to the TimeTable endpoint. "
                        "Ensure your API credentials have the required scope.",
                    ) from None
            all_entries.extend(entries)

        # Deduplicate: same (week, day, period) may appear multiple times
        # Keep the most recent entry (latest start_date)
        seen: dict[tuple, Any] = {}
        for e in all_entries:
            key = (
                getattr(e, "week_display_name", None) or getattr(e, "week_number", "1"),
                e.day_of_week,
                e.period_display_name or "",
            )
            existing = seen.get(key)
            if existing is None or (e.period_start_date or "") > (existing.period_start_date or ""):
                seen[key] = e

        # Resolve staff codes
        live_staff_ids = {getattr(e, "staff_id", None) for e in seen.values() if getattr(e, "staff_id", None)}
        staff_codes = _fetch_staff_codes(self._http, live_staff_ids, school_id)

        # Build the grid
        timetable: dict[str, dict[str, list[Slot]]] = {}
        for (week_name, day, _), e in seen.items():
            week = str(week_name) if week_name else "Week 1"
            if not day:
                continue
            if week not in timetable:
                timetable[week] = {}
            if day not in timetable[week]:
                timetable[week][day] = []

            is_cover = bool(getattr(e, "is_cover", 0))
            sid = getattr(e, "staff_id", None)
            raw_room = getattr(e, "location_name", None)
            room = raw_room if raw_room and raw_room.upper() != "DEFAULT" else None
            # Use class_name from the entry, not class_staff_room (which has newlines)
            cls_name = getattr(e, "class_name", None) or getattr(e, "class_staff_room", None)
            if cls_name:
                cls_name = cls_name.replace("\r", "").replace("\n", " ").strip()
            timetable[week][day].append(Slot(
                period=e.period_display_name or "",
                start_time=_format_time(e.period_start_time),
                end_time=_format_time(e.period_end_time),
                class_name=cls_name,
                room=room,
                staff_code=staff_codes.get(sid) if sid else None,
                teacher_id=sid,
                is_cover=is_cover,
            ))

        # Sort
        for week in timetable:
            for day in timetable[week]:
                timetable[week][day].sort(key=lambda s: s.start_time)
            timetable[week] = dict(
                sorted(timetable[week].items(), key=lambda kv: DAY_ORDER.index(kv[0]) if kv[0] in DAY_ORDER else 99)
            )

        return timetable


class AsyncTimetableHelper:
    """Asynchronous timetable helper.

    Pass either an ``AsyncBromcomClient`` or its underlying ``AsyncHttpClient``.
    """

    def __init__(self, client_or_http: Any) -> None:
        self._http = _resolve_http(client_or_http)

    async def get_student_template(self, student: Any, from_date: str | date | None = None) -> list[TimetableBlock]:
        return await self.get_template(person_id=student.person_id, from_date=from_date)

    async def get_staff_template(self, staff: Any, from_date: str | date | None = None) -> list[TimetableBlock]:
        return await self.get_template(person_id=staff.staff_id, from_date=from_date)

    async def get_template(
        self,
        person_id: int,
        from_date: str | date | None = None,
        school_id: int | None = None,
    ) -> list[TimetableBlock]:
        """Async version of TimetableHelper.get_template."""
        from bromcom.models.collection import CollectionAssociates, CollectionTimetables
        from bromcom.models.timetable import PeriodStructures

        if from_date is None:
            from_date = _next_weekday(date.today())
        elif isinstance(from_date, str):
            from_date = _parse_date(from_date)

        try:
            cycle_length = await _get_cycle_length_async(self._http, school_id)
        except BromcomScopeError:
            raise BromcomScopeError(
                403,
                "Timetables require access to the PeriodStructures endpoint. "
                "Ensure your API credentials have the required scope.",
            ) from None
        window_end = from_date + timedelta(weeks=cycle_length)

        try:
            associates = await self._http.get(
                "/v2/CollectionAssociates",
                entity_filter=f"personID={person_id}",
                school_id=school_id,
                model=CollectionAssociates,
            )
        except BromcomScopeError:
            raise BromcomScopeError(
                403,
                "Template timetables require access to the CollectionAssociates endpoint. "
                "Ensure your API credentials have the required scope.",
            ) from None

        active = []
        for a in associates:
            start = _parse_date(a.start_date) if a.start_date else date.min
            end = _parse_date(a.end_date) if a.end_date else date.max
            if start < window_end and end >= from_date:
                active.append(a)

        if not active:
            return []

        boundaries = {from_date, window_end}
        for a in active:
            start = _parse_date(a.start_date) if a.start_date else date.min
            end = _parse_date(a.end_date) if a.end_date else date.max
            if from_date < start < window_end:
                boundaries.add(start)
            if from_date < end < window_end:
                boundaries.add(end + timedelta(days=1))
        boundaries = sorted(boundaries)

        coll_ids = list({a.collection_id for a in active})
        all_ct: list = []
        for coll_id in coll_ids:
            ct = await self._http.get(
                "/v2/CollectionTimetables",
                entity_filter=f"collectionID={coll_id}",
                school_id=school_id,
                model=CollectionTimetables,
            )
            for t in ct:
                t._collection_id = coll_id
            all_ct.extend(ct)

        cal_model_ids = list({t.calendar_model_id for t in all_ct})
        period_map: dict[int, list] = {}
        for cal_id in cal_model_ids:
            ps = await self._http.get(
                "/v2/PeriodStructures",
                entity_filter=f"calendarModelID={cal_id}",
                school_id=school_id,
                model=PeriodStructures,
            )
            period_map[cal_id] = _filter_periods(ps)

        coll_names = {a.collection_id: a.collection_name for a in active}

        # Resolve staff codes and location names
        staff_ids = {t.employee_id for t in all_ct if t.employee_id}
        location_ids = {t.location_id for t in all_ct if t.location_id}
        staff_codes = await _fetch_staff_codes_async(self._http, staff_ids, school_id)
        location_names = await _fetch_locations_async(self._http, location_ids, school_id)

        blocks = []
        for i in range(len(boundaries) - 1):
            block_start = boundaries[i]
            block_end = boundaries[i + 1] - timedelta(days=1)

            block_colls = set()
            for a in active:
                a_start = _parse_date(a.start_date) if a.start_date else date.min
                a_end = _parse_date(a.end_date) if a.end_date else date.max
                if a_start <= block_end and a_end >= block_start:
                    block_colls.add(a.collection_id)

            timetable: dict[str, dict[str, list[Slot]]] = {}
            for ct in all_ct:
                if ct._collection_id not in block_colls:
                    continue
                ct_start = _parse_date(ct.start_date) if ct.start_date else date.min
                ct_end = _parse_date(ct.end_date) if ct.end_date else date.max
                if ct_start > block_end or ct_end < block_start:
                    continue

                periods = period_map.get(ct.calendar_model_id, [])
                for p in periods:
                    week = p.week_display_name or f"Week {p.week_number or 1}"
                    day = p.day_of_week
                    if not day:
                        continue
                    if week not in timetable:
                        timetable[week] = {}
                    if day not in timetable[week]:
                        timetable[week][day] = []
                    timetable[week][day].append(Slot(
                        period=_derive_period_name(p),
                        start_time=_format_time(p.default_start_time),
                        end_time=_format_time(p.default_end_time),
                        class_name=coll_names.get(ct._collection_id),
                        room=location_names.get(ct.location_id) if ct.location_id else None,
                        staff_code=staff_codes.get(ct.employee_id) if ct.employee_id else None,
                        teacher_id=ct.employee_id,
                    ))

            for week in timetable:
                for day in timetable[week]:
                    seen_slots: set[tuple] = set()
                    unique: list[Slot] = []
                    for s in timetable[week][day]:
                        key = (s.start_time, s.end_time, s.class_name)
                        if key not in seen_slots:
                            seen_slots.add(key)
                            unique.append(s)
                    timetable[week][day] = sorted(unique, key=lambda s: s.start_time)
                timetable[week] = dict(
                    sorted(timetable[week].items(), key=lambda kv: DAY_ORDER.index(kv[0]) if kv[0] in DAY_ORDER else 99)
                )

            if timetable:
                blocks.append(TimetableBlock(
                    valid_from=block_start.isoformat(),
                    valid_to=block_end.isoformat(),
                    timetable=timetable,
                ))

        return blocks

    async def get_live(
        self,
        student_id: int | None = None,
        staff_id: int | None = None,
        from_date: str | date | None = None,
        include_cover: bool = True,
        school_id: int | None = None,
    ) -> dict[str, dict[str, list[Slot]]]:
        """Async version of TimetableHelper.get_live."""
        if student_id is None and staff_id is None:
            raise ValueError("Either student_id or staff_id must be provided")

        if from_date is None:
            from_date = _next_weekday(date.today())
        elif isinstance(from_date, str):
            from_date = _parse_date(from_date)

        try:
            cycle_length = await _get_cycle_length_async(self._http, school_id)
        except BromcomScopeError:
            raise BromcomScopeError(
                403,
                "Timetables require access to the PeriodStructures endpoint. "
                "Ensure your API credentials have the required scope.",
            ) from None

        all_entries = []
        monday = _monday_of(from_date)
        for week_idx in range(cycle_length):
            week_start = monday + timedelta(weeks=week_idx)
            week_end = week_start + timedelta(days=4)
            date_filter = (
                f"periodStartDate >= '{week_start.isoformat()}' "
                f"and periodStartDate <= '{week_end.isoformat()}'"
            )

            if student_id is not None:
                base_filter = f"studentID={student_id} and {date_filter}"
                from bromcom.models.student import StudentTimetables
                try:
                    entries = await self._http.get(
                        "/v2/StudentTimetables",
                        entity_filter=base_filter,
                        school_id=school_id,
                        model=StudentTimetables,
                    )
                except BromcomScopeError:
                    raise BromcomScopeError(
                        403,
                        "Live student timetables require access to the StudentTimetables endpoint. "
                        "Ensure your API credentials have the required scope.",
                    ) from None
            else:
                base_filter = f"staffID={staff_id} and {date_filter}"
                if not include_cover:
                    base_filter += " and isCover=0"
                from bromcom.models.timetable import TimeTable
                try:
                    entries = await self._http.get(
                        "/v2/TimeTable",
                        entity_filter=base_filter,
                        school_id=school_id,
                        model=TimeTable,
                    )
                except BromcomScopeError:
                    raise BromcomScopeError(
                        403,
                        "Live staff timetables require access to the TimeTable endpoint. "
                        "Ensure your API credentials have the required scope.",
                    ) from None
            all_entries.extend(entries)

        seen: dict[tuple, Any] = {}
        for e in all_entries:
            key = (
                getattr(e, "week_display_name", None) or getattr(e, "week_number", "1"),
                e.day_of_week,
                e.period_display_name or "",
            )
            existing = seen.get(key)
            if existing is None or (e.period_start_date or "") > (existing.period_start_date or ""):
                seen[key] = e

        # Resolve staff codes
        live_staff_ids = {getattr(e, "staff_id", None) for e in seen.values() if getattr(e, "staff_id", None)}
        staff_codes = await _fetch_staff_codes_async(self._http, live_staff_ids, school_id)

        timetable: dict[str, dict[str, list[Slot]]] = {}
        for (week_name, day, _), e in seen.items():
            week = str(week_name) if week_name else "Week 1"
            if not day:
                continue
            if week not in timetable:
                timetable[week] = {}
            if day not in timetable[week]:
                timetable[week][day] = []

            is_cover = bool(getattr(e, "is_cover", 0))
            sid = getattr(e, "staff_id", None)
            raw_room = getattr(e, "location_name", None)
            room = raw_room if raw_room and raw_room.upper() != "DEFAULT" else None
            cls_name = getattr(e, "class_name", None) or getattr(e, "class_staff_room", None)
            if cls_name:
                cls_name = cls_name.replace("\r", "").replace("\n", " ").strip()
            timetable[week][day].append(Slot(
                period=e.period_display_name or "",
                start_time=_format_time(e.period_start_time),
                end_time=_format_time(e.period_end_time),
                class_name=cls_name,
                room=room,
                staff_code=staff_codes.get(sid) if sid else None,
                teacher_id=sid,
                is_cover=is_cover,
            ))

        for week in timetable:
            for day in timetable[week]:
                timetable[week][day].sort(key=lambda s: s.start_time)
            timetable[week] = dict(
                sorted(timetable[week].items(), key=lambda kv: DAY_ORDER.index(kv[0]) if kv[0] in DAY_ORDER else 99)
            )

        return timetable
