"""Optional helpers for the Bromcom Partner Data API Python client.

Currently exposes timetable helpers. These live outside the core ``bromcom``
package because their assumptions (e.g. a Collections-driven school) are
opinionated rather than universal.
"""

from bromcom_helpers.timetables import (
    AsyncTimetableHelper,
    Slot,
    TimetableBlock,
    TimetableHelper,
)

__all__ = [
    "AsyncTimetableHelper",
    "Slot",
    "TimetableBlock",
    "TimetableHelper",
]
