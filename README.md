# Bromcom Python Helpers

Optional helpers that build on top of the [`bromcom-api`](../python) Python client.

These helpers live in a separate package because their assumptions are
opinionated rather than universal — for example, the timetable helpers assume
a Collections-driven school. Schools that don't use Bromcom Collections need a
different approach (modal aggregation across multiple cycles), which is why
this code is on a separate release track.

## Installation

```bash
pip install git+https://github.com/nickwatson/bromcom-python-helpers.git@v0.0.1
```

`bromcom-api` is a runtime dependency and is pulled in transitively (pinned to the matching core version).

Submodule install also works if you'd rather vendor the source:

```bash
git submodule add git@github.com:nickwatson/bromcom-python-helpers.git
pip install -e ./bromcom-python-helpers
```

## Timetables

Two timetable views: template (repeating schedule from definitions) and live
(actual lessons).

```python
from bromcom import BromcomClient
from bromcom_helpers import TimetableHelper, AsyncTimetableHelper

client = BromcomClient(app_id="...", app_secret="...", school_id=20001)
timetables = TimetableHelper(client)

# Template timetable (from Collection definitions)
blocks = timetables.get_template(person_id=123)
blocks = timetables.get_student_template(student)
blocks = timetables.get_staff_template(staff_member)
# Returns list of TimetableBlock with valid_from/to and nested week→day→slots

# Live timetable (current lessons, covers, room changes)
grid = timetables.get_live(student_id=123)
grid = timetables.get_live(staff_id=456, include_cover=False)
# Returns dict: week → day → list of Slot
```

`AsyncTimetableHelper` accepts an `AsyncBromcomClient` and exposes the same
methods as coroutines.

**Required endpoints:**

- Template: `CollectionAssociates`, `CollectionTimetables`, `PeriodStructures`, `Staff`, `Locations` (GET)
- Live student: `StudentTimetables`, `PeriodStructures`, `Staff` (GET)
- Live staff: `TimeTable`, `PeriodStructures`, `Staff` (GET)

If any required endpoint is not accessible, a `BromcomScopeError` is raised
with a message indicating which endpoint is needed.
