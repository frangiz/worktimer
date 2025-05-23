import contextlib
import subprocess
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional, Tuple

from dotenv import dotenv_values
from pydantic import BaseModel, Field, RootModel
from rich.console import Console
from rich.table import Table

DEFAULT_LUNCH_DURATION = 30
MAX_PROJECT_NAME_LENGTH = 50
DEFAULT_WORK_HOURS = 8
console = Console(width=120)


class Config:
    mode: str
    datafile_dir: Path
    datafile: str
    workhours_one_day: int

    def __init__(self) -> None:
        self.reload()

    def reload(self):
        # Set default values
        self.mode = ""
        self.datafile_dir = Path(Path.home(), ".worktimer")
        self.datafile = (
            f"{datetime.now().year}-{datetime.now().month:02d}-timesheet.json"
        )

        self.workhours_one_day = DEFAULT_WORK_HOURS

        # Override default values
        config = dotenv_values("config.env")
        if config.get("mode", "") == "dev":
            self.mode = "dev"
            self.datafile_dir = Path(".worktimer")


cfg = Config()


def _today_iso_format() -> str:
    return date.today().isoformat()


def time_diff(t1: Optional[time], t2: Optional[time]) -> int:
    if t1 is None or t2 is None:
        return 0
    some_date = date(1, 1, 1)
    datetime1 = datetime.combine(some_date, t1)
    datetime2 = datetime.combine(some_date, t2)
    return int((datetime1 - datetime2).total_seconds()) // 60


class WorkBlock(BaseModel):
    start: Optional[time] = None
    stop: Optional[time] = None
    comment: Optional[str] = None
    project_id: Optional[int] = None
    lunch: int = 0

    @property
    def worked_time(self) -> int:
        if not self.started() or not self.stopped():
            return 0
        return time_diff(self.stop, self.start) - self.lunch

    def started(self) -> bool:
        return self.start is not None

    def stopped(self) -> bool:
        return self.stop is not None

    def is_ongoing(self) -> bool:
        return self.started() and not self.stopped()


class Day(BaseModel):
    this_date: date
    flex_minutes: int = 0
    work_blocks: List[WorkBlock] = Field(default_factory=lambda: [])
    time_off_minutes: int = 0

    @property
    def last_work_block(self) -> WorkBlock:
        if len(self.work_blocks) == 0:
            self.work_blocks.append(WorkBlock())
        return self.work_blocks[-1]

    @property
    def worked_time(self) -> int:
        return sum(wt.worked_time for wt in self.work_blocks)

    @property
    def lunch(self) -> int:
        return sum(wt.lunch for wt in self.work_blocks)

    def recalc_flex(self) -> None:
        expected_worktime_in_mins = cfg.workhours_one_day * 60
        time_off_minutes = self.time_off_minutes
        weekday = self.this_date.isoweekday()
        # Check if weekend
        if weekday in (6, 7):
            expected_worktime_in_mins = 0
            time_off_minutes = 0
        if len(self.work_blocks) == 1 and not self.last_work_block.stopped():
            self.flex_minutes = 0
        else:
            self.flex_minutes = (
                self.worked_time - expected_worktime_in_mins + time_off_minutes
            )


class Timesheet(BaseModel):
    days: Dict[str, Day] = Field(default_factory=lambda: {})
    target_hours: int = 167

    @property
    def monthly_flex(self) -> int:
        return sum(d.flex_minutes for d in self.days.values())

    @property
    def today(self) -> Day:
        today = _today_iso_format()
        if today not in self.days:
            self.days[today] = Day(this_date=datetime.now().date())
        return self.days[today]

    def get_day(self, key: str) -> Day:
        if key not in self.days:
            self.days[key] = Day(this_date=date.fromisoformat(key))
        return self.days[key]

    def get_days(self, start_date: str, end_date: str) -> List[Day]:
        days: List[Day] = []
        from_date = date.fromisoformat(start_date)
        to_date = date.fromisoformat(end_date)
        while from_date <= to_date:
            days.append(self.get_day(from_date.isoformat()))
            from_date += timedelta(days=1)
        return days


class Project(BaseModel):
    id: int
    name: str
    deleted: bool = False

    def delete(self) -> None:
        self.deleted = True


class Projects(RootModel):
    root: List[Project]

    def __iter__(self):
        return iter(self.root)

    def __len__(self) -> int:
        return len(self.root)

    def __getitem__(self, index: int) -> Project:
        return self.root[index]

    def add_project(self, project: Project) -> None:
        self.root.append(project)

    def get_project_by_id(self, project_id: int) -> Project:
        for p in self.root:
            if p.id == project_id:
                return p
        raise ValueError(f"No project with id {project_id}")


class ViewSpans(Enum):
    TODAY = auto()
    WEEK = auto()
    PREV_WEEK = auto()
    MONTH = auto()


class RecalcAction(Enum):
    FLEX = 1


class DateRange:
    @staticmethod
    def get_range(
        view_span: ViewSpans, base_date: Optional[date] = None
    ) -> Tuple[date, date]:
        today = base_date or datetime.now().date()

        if view_span == ViewSpans.TODAY:
            return today, today

        if view_span == ViewSpans.WEEK:
            start_date = today - timedelta(days=today.isoweekday() - 1)
            return start_date, today

        if view_span == ViewSpans.PREV_WEEK:
            start_date = today - timedelta(days=today.isoweekday() + 6)
            end_date = start_date + timedelta(days=6)
            return start_date, end_date

        if view_span == ViewSpans.MONTH:
            start_date = today.replace(day=1)
            # Last day of current month
            if today.month == 12:
                last_day = today.replace(
                    year=today.year + 1, month=1, day=1
                ) - timedelta(days=1)
            else:
                last_day = today.replace(month=today.month + 1, day=1) - timedelta(
                    days=1
                )
            return start_date, last_day

        raise ValueError(f"Invalid view span: {view_span}")

    @staticmethod
    def get_remaining_days_in_week_in_same_month(start_date: date) -> List[date]:
        days = []
        current_date = start_date
        while current_date.month == start_date.month:
            days.append(current_date)
            if current_date.isoweekday() == 7:
                break
            current_date += timedelta(days=1)

        return days


def load_timesheet(datafile: Optional[str] = None) -> Timesheet:
    if datafile is None:
        datafile = cfg.datafile
    if not cfg.datafile_dir.joinpath(datafile).is_file():
        empty_ts = Timesheet()
        save_timesheet(empty_ts)
    with open(cfg.datafile_dir.joinpath(datafile), "r") as f:
        json_content = f.read()
    return Timesheet.model_validate_json(json_content)


def save_timesheet(ts: Timesheet, datafile: Optional[str] = None) -> None:
    if datafile is None:
        datafile = cfg.datafile
    with open(cfg.datafile_dir.joinpath(datafile), "w+", encoding="utf-8") as f:
        f.write(ts.model_dump_json(indent=4))


def load_projects() -> Projects:
    projects_file = cfg.datafile_dir.joinpath("projects.json")
    if not projects_file.is_file():
        projects = Projects([])
        save_projects(projects)
    with open(projects_file, "r") as f:
        json_content = f.read()
    return Projects.model_validate_json(json_content)


def save_projects(projects: Projects) -> None:
    with open(cfg.datafile_dir.joinpath("projects.json"), "w+", encoding="utf-8") as f:
        f.write(projects.model_dump_json(indent=4))


def get_time_and_comment(params):
    try:
        if params:
            h, m = map(int, params[0].split(":"))
            time = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
            comment = " ".join(params[1:]) if len(params) > 1 else None
        else:
            time = datetime.now().replace(second=0, microsecond=0)
            comment = None
        return time, comment
    except ValueError:
        raise ValueError("Invalid time format. Expected format is hh:mm")


def handle_command(cmd: str) -> None:
    if cmd == "":
        print("No command given")
        return
    command_map = {
        "start": lambda params: start(get_time_and_comment(params)[0]),
        "stop": lambda params: stop(*get_time_and_comment(params)),
        "switch": lambda params: switch(get_time_and_comment(params)[0]),
        "lunch": lambda params: (
            lunch(int(params[0])) if params else lunch(DEFAULT_LUNCH_DURATION)
        ),
        "edit": lambda _: edit(),
        "view": lambda params: view(ViewSpans[params[0].upper()]) if params else view(),
        "summary": lambda _: summary(),
        "recalc": lambda params: (
            recalc(RecalcAction[params[0].upper()]) if params else recalc()
        ),
        "timeoff": lambda params: set_time_off(int(params[0]) * 60),
        "target_hours": lambda params: set_target_hours(int(params[0])),
        "comment": lambda params: set_comment(" ".join(params) if params else None),
        "create_project": lambda params: create_project(" ".join(params)),
        "list_projects": lambda _: list_projects(),
        "delete_project": lambda params: delete_project(int(params[0])),
        "rename_project": lambda params: rename_project(" ".join(params)),
        "project_summary": lambda params: project_summary(
            ViewSpans[params[0].upper()] if params else ViewSpans.WEEK
        ),
        "help": lambda _: print_menu(),
    }

    cmd, *params = cmd.split()

    if cmd in command_map:
        command_map[cmd](params)
    else:
        print(f"Unknown command: {cmd}")


def _print_estimated_endtime_for_today(
    work_blocks: List[WorkBlock], lunch: int = DEFAULT_LUNCH_DURATION, timeoff: int = 0
) -> None:
    mins_left_to_work = (
        (cfg.workhours_one_day * 60)
        - timeoff
        - sum(wt.worked_time for wt in work_blocks)
    )
    if not work_blocks[-1].start:
        return
    actual_lunch = sum(wt.lunch for wt in work_blocks)
    estimated_lunch_left_today = 0 if actual_lunch else lunch
    work_end_with_lunch = (
        (
            datetime.combine(date.today(), work_blocks[-1].start)
            + timedelta(minutes=mins_left_to_work)
            + timedelta(minutes=estimated_lunch_left_today)
        )
        .time()
        .replace(second=0, microsecond=0)
    )
    todays_lunch = actual_lunch if actual_lunch else lunch
    print(
        f"Estimated end time for today with {todays_lunch}"
        f" min lunch is {work_end_with_lunch}"
    )


def start(start_time: datetime) -> None:
    ts = load_timesheet()
    last_wb = ts.today.last_work_block
    if last_wb.started() and not last_wb.stopped():
        print("Workblock already started, stop it before starting another one")
        return

    projects = load_projects()
    project_id = prompt_for_project() if len(projects) else None
    if last_wb.stopped():
        ts.today.work_blocks.append(
            WorkBlock(start=start_time.time(), project_id=project_id)
        )
    else:
        last_wb.start = start_time.time()
        last_wb.project_id = project_id

    print(f"Starting at {start_time}")
    save_timesheet(ts)
    if ts.today.lunch > 0:
        _print_estimated_endtime_for_today(
            work_blocks=ts.today.work_blocks,
            lunch=ts.today.lunch,
            timeoff=ts.today.time_off_minutes,
        )
    else:
        _print_estimated_endtime_for_today(
            work_blocks=ts.today.work_blocks, timeoff=ts.today.time_off_minutes
        )


def stop(stop_time: datetime, comment: Optional[str] = None) -> None:
    ts = load_timesheet()
    if not ts.today.last_work_block.started():
        print("Could not stop workblock, is your last workblock started?")
        return
    if ts.today.last_work_block.stopped():
        return
    print(f"Stopping at {stop_time}")

    projects = load_projects()
    current_project_id = ts.today.last_work_block.project_id
    project_id = prompt_for_project(current_project_id) if len(projects) else None

    ts.today.last_work_block.stop = stop_time.time()
    ts.today.last_work_block.comment = comment
    ts.today.last_work_block.project_id = project_id
    ts.today.recalc_flex()

    flex_hours = abs(ts.today.flex_minutes) // 60
    flex_mins = abs(ts.today.flex_minutes) % 60
    if ts.today.flex_minutes >= 0:
        print(f"Flex for today: {flex_hours} hours {flex_mins} mins")
    else:
        print(f"Flex for today is negative: {flex_hours} hours {flex_mins} mins")
    save_timesheet(ts)


def switch(switch_time: Optional[datetime] = None) -> None:
    """Stop current workblock and start new one at specified time."""
    if switch_time is None:
        switch_time = datetime.now().replace(second=0, microsecond=0)

    ts = load_timesheet()
    if not ts.today.last_work_block.started():
        raise ValueError("No active work block to switch from")
    if ts.today.last_work_block.stopped():
        return

    # Validate switch time
    current_start = ts.today.last_work_block.start
    if current_start is None:
        raise ValueError("Current workblock has no start time")

    if switch_time.time() < current_start:
        raise ValueError(
            f"Switch time {switch_time.strftime('%H:%M')} cannot be before workblock "
            f"start time {current_start.strftime('%H:%M')}"
        )

    print(f"Switching at {switch_time.strftime('%H:%M')}")

    # Update current workblock
    projects = load_projects()
    if len(projects):
        print("Select project for current workblock:")
        current_project_id = prompt_for_project(ts.today.last_work_block.project_id)
        ts.today.last_work_block.project_id = current_project_id

        print("Select project for new workblock:")
        new_project_id = prompt_for_project()
    else:
        current_project_id = None
        new_project_id = None

    # Stop current and start new workblock
    ts.today.last_work_block.stop = switch_time.time()
    ts.today.work_blocks.append(
        WorkBlock(start=switch_time.time(), project_id=new_project_id)
    )

    ts.today.recalc_flex()
    save_timesheet(ts)

    if len(projects) and new_project_id is not None:
        print(f"Switching to project {projects.get_project_by_id(new_project_id).name}")


def lunch(lunch_mins: int) -> None:
    ts = load_timesheet()

    if len(ts.today.work_blocks) == 0 or not ts.today.last_work_block.started():
        print("Could not find today in timesheet, did you start the day?")
        return
    if ts.today.lunch != 0:
        return
    # TODO: Check that lunch duration is not too long

    _print_estimated_endtime_for_today(ts.today.work_blocks, lunch_mins)

    ts.today.last_work_block.lunch = lunch_mins
    ts.today.recalc_flex()
    save_timesheet(ts)
    print(f"Added {lunch_mins} mins as lunch")


def edit(editor: str = "vim") -> None:
    try:
        filepath = cfg.datafile_dir.joinpath(cfg.datafile)
        subprocess.run([editor, filepath], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error opening editor: {e}")
    except FileNotFoundError:
        print(f"Editor {editor} not found")


def view(view_span: ViewSpans = ViewSpans.TODAY) -> None:
    start_date, end_date = DateRange.get_range(view_span)
    ts = load_timesheet()
    days = ts.get_days(start_date.isoformat(), end_date.isoformat())

    print_days(days)
    if view_span in [ViewSpans.WEEK, ViewSpans.PREV_WEEK]:
        _print_footer(days)


def summary() -> None:
    ts = load_timesheet()
    days: List[Day] = []
    for n in range(date.today().day - 1, -1, -1):
        days.append(ts.get_day((date.today() - timedelta(days=n)).isoformat()))

    table = Table()
    table.add_column("week")
    table.add_column("date")
    table.add_column("worked time")
    table.add_column("daily flex")
    table.add_column("time off")

    for d in days:
        the_date = d.this_date.isoformat()
        worked_time = fmt_mins(d.worked_time, expand=True) if d.worked_time > 0 else ""
        daily_flex = fmt_mins(d.flex_minutes) if d.worked_time > 0 else ""
        timeoff = fmt_mins(d.time_off_minutes) if d.time_off_minutes > 0 else ""
        week = d.this_date.isocalendar()[1] if d.this_date.isoweekday() == 1 else ""
        if d.this_date.isoweekday() == 1:
            table.add_section()
        table.add_row(str(week), the_date, worked_time, daily_flex, timeoff)
    console.print(table)
    print("")

    # summarize weeks
    weekly_summary: DefaultDict[int, int] = defaultdict(int)
    for d in days:
        if d.worked_time > 0:
            weekly_summary[d.this_date.isocalendar()[1]] += d.worked_time
    for week, weekly_time in weekly_summary.items():
        print(f"week {week}: {fmt_mins(weekly_time)}")

    # summarize month
    expected_worked_hours_sum = (
        sum(
            (cfg.workhours_one_day * 60 - d.time_off_minutes)
            for d in days
            if d.worked_time > 0
        )
        // 60
    )
    print(
        (
            f"Worked {fmt_mins(sum(d.worked_time for d in days))} "
            f"of {expected_worked_hours_sum} hour(s) => "
            f"monthly flex: {fmt_mins(sum(d.flex_minutes for d in days))}"
        )
    )
    print(f"Target hours for month: {ts.target_hours}")


def recalc(action: RecalcAction = RecalcAction.FLEX) -> None:
    if action == RecalcAction.FLEX:
        curr_year = _today_iso_format()[:4]
        for f in cfg.datafile_dir.glob(f"{curr_year}-*-timesheet.json"):
            ts = load_timesheet(f.name)
            for _, v in ts.days.items():
                v.recalc_flex()
            save_timesheet(ts, f.name)


def set_time_off(time_off_mins: int) -> None:
    if time_off_mins < 0 or time_off_mins > cfg.workhours_one_day * 60:
        raise ValueError(
            "Invalid timeoff value, must be an int between 0 and 8 inclusive."
        )
    ts = load_timesheet()
    ts.today.time_off_minutes = time_off_mins
    ts.today.recalc_flex()
    save_timesheet(ts)
    print(f"Setting timeoff to {fmt_mins(time_off_mins)}")


def set_target_hours(target_hours: int) -> None:
    if target_hours < 0:
        raise ValueError("Invalid target_hours value, must be an int greater than 0.")
    ts = load_timesheet()
    ts.target_hours = target_hours
    save_timesheet(ts)
    print(f"Setting target hours to {target_hours}")


def set_comment(text: Optional[str]) -> None:
    ts = load_timesheet()
    if ts.today.last_work_block.started() and not ts.today.last_work_block.stopped():
        ts.today.last_work_block.comment = text
    else:
        print("Cannot set comment for workblock not started")
    save_timesheet(ts)


def create_project(name: Optional[str]) -> None:
    if not name:
        raise ValueError("Project name cannot be empty")
    if len(name) > MAX_PROJECT_NAME_LENGTH:
        raise ValueError(
            f"Project name cannot be longer than {MAX_PROJECT_NAME_LENGTH} characters"
        )
    projects = load_projects()
    if any(p.name == name for p in projects):
        raise ValueError(f"Project with name '{name}' already exists")
    new_id = max((p.id for p in projects), default=0) + 1
    projects.add_project(Project(id=new_id, name=name, deleted=False))
    save_projects(projects)


def list_projects() -> None:
    projects = load_projects()
    for p in projects:
        if p.deleted:
            continue
        print(f"{p.id}: {p.name}")


def delete_project(project_id: int) -> None:
    projects = load_projects()
    projects.get_project_by_id(project_id).delete()
    save_projects(projects)


def rename_project(params: str) -> None:
    parts = params.split(maxsplit=1)
    if not parts:
        raise ValueError("No project id provided")
    if len(parts) < 2:
        raise ValueError("Project name cannot be empty")

    project_id = int(parts[0])
    new_name = parts[1]
    if len(new_name) > MAX_PROJECT_NAME_LENGTH:
        raise ValueError(
            f"Project name cannot be longer than {MAX_PROJECT_NAME_LENGTH} characters"
        )
    projects = load_projects()
    if any(p.name == new_name for p in projects):
        raise ValueError(f"Project with name '{new_name}' already exists")
    projects.get_project_by_id(project_id).name = new_name
    save_projects(projects)


def project_summary(view_span: ViewSpans = ViewSpans.WEEK) -> None:
    start_date, end_date = DateRange.get_range(view_span)
    ts = load_timesheet()
    projects = load_projects()

    for week_start_date in get_week_start_dates(start_date, end_date):
        _print_project_summary_week(week_start_date, ts, projects)
        print("")

    if view_span == ViewSpans.MONTH:
        print("Month total:")
        # Calculate project times for the entire month
        project_month_totals: Dict[int, int] = defaultdict(int)
        for day in ts.get_days(start_date.isoformat(), end_date.isoformat()):
            for block in day.work_blocks:
                project_id = block.project_id or 0
                project_month_totals[project_id] += block.worked_time

        # Sort projects and print totals
        for project in sorted(
            [p for p in projects if not p.deleted], key=lambda p: p.name
        ):
            if (
                project.id in project_month_totals
                and project_month_totals[project.id] > 0
            ):
                print(f"  {project.name}: {fmt_mins(project_month_totals[project.id])}")


def _print_project_summary_week(
    start_date: date, ts: Timesheet, projects: Projects
) -> None:
    days_to_print = DateRange.get_remaining_days_in_week_in_same_month(start_date)

    table = Table()
    table.add_column("Project")
    for current in days_to_print:
        table.add_column(f"{current.strftime('%a %d')}")
    table.add_column("Total")

    # Calculate project times
    project_times: DefaultDict[int, DefaultDict[date, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for day in ts.get_days(start_date.isoformat(), days_to_print[-1].isoformat()):
        for block in day.work_blocks:
            if block.project_id:
                project_times[block.project_id][day.this_date] += block.worked_time
            else:
                project_times[0][day.this_date] += block.worked_time

    # Add rows with proper formatting
    projects.add_project(Project(id=0, name="no project"))
    for project in projects:
        if project.id not in project_times:
            continue
        row = [project.name]
        total = 0
        for current in days_to_print:
            mins = project_times[project.id][current]
            total += mins
            row.append(fmt_mins(mins) if mins else "")
        row.append(fmt_mins(total))
        table.add_row(*row)
        table.add_section()

    # Add total row
    totals = ["Total"]
    grand_total = 0
    for current in days_to_print:
        day_total = sum(times[current] for times in project_times.values())
        grand_total += day_total
        totals.append(fmt_mins(day_total) if day_total else "")
    totals.append(fmt_mins(grand_total))
    table.add_row(*totals)

    console.print(f"Week {start_date.isocalendar()[1]}")
    console.print(table)


def get_week_start_dates(start_date: date, end_date: date) -> List[date]:
    """
    Returns a list containing the start_date and all Mondays in the interval
    [start_date, end_date].

    Args:
        start_date: The start of the interval
        end_date: The end of the interval

    Returns:
        List of dates containing start_date and all Mondays within the interval
    """
    result = [start_date]  # Always include the start_date

    # If start_date is already a Monday, we'll find the next Monday
    # If not, find the first Monday after start_date
    days_until_monday = (8 - start_date.isoweekday()) % 7

    # If days_until_monday is 0, it means start_date is a Monday
    # and we already included it in the result
    if days_until_monday == 0:
        # The next Monday is 7 days from the start_date
        first_monday = start_date + timedelta(days=7)
    else:
        # Calculate the first Monday after start_date
        first_monday = start_date + timedelta(days=days_until_monday)

    # Add all Mondays in the interval
    current = first_monday
    while current <= end_date:
        result.append(current)
        current += timedelta(days=7)

    return result


def prompt_for_project(default_project_id: Optional[int] = None) -> Optional[int]:
    projects = load_projects()
    print("Select project:")
    print("0: No project")
    for p in projects:
        if p.deleted:
            continue
        print(f"{p.id}: {p.name}")
    if default_project_id is not None:
        default_project_name = next(
            p.name for p in projects if p.id == default_project_id
        )
        print(f"Press enter to keep {default_project_name}")

    while True:
        try:
            user_input = input("> ")
            if user_input == "" and default_project_id is not None:
                return default_project_id
            project_id = int(user_input)
            if project_id == 0 or project_id in (p.id for p in projects):
                return project_id if project_id != 0 else None
        except ValueError:
            print("Invalid project id")


def calc_total_flex() -> int:
    return sum(
        load_timesheet(f.name).monthly_flex
        for f in cfg.datafile_dir.glob("*-timesheet.json")
    )


def total_flex_as_str() -> str:
    return fmt_mins(calc_total_flex())


def print_menu():
    print("-- commands --")
    print("start [hh:mm]")
    print("stop [hh:mm]")
    print("switch [hh:mm]")
    print("lunch [n]")
    print("edit")
    print("view [TODAY|WEEK]")
    print("summary [MONTH]")
    print("recalc [FLEX]")
    print("timeoff [hours]")
    print("target_hours [hours]")
    print("comment [the comment]")
    print("create_project [name]")
    print("list_projects")
    print("delete_project [id]")
    print("rename_project [id]")


def print_days(days: List[Day]) -> None:
    if len(days) == 1:
        _print_day(days[0])
        return
    for day in days[:-1]:
        _print_day(day)
        print("")
    _print_day(days[-1])


def _print_day(day: Day) -> None:
    header = " | ".join(
        [
            day.this_date.isoformat(),
            f"worked time: {fmt_mins(day.worked_time, expand=True)}",
            f"lunch: {fmt_mins(day.lunch)}",
            f"daily flex: {fmt_mins(day.flex_minutes)}",
        ]
    )
    print(header)
    _print_work_blocks(day.work_blocks)


def _print_footer(days: List[Day]) -> None:
    weekly_flex = sum(d.flex_minutes for d in days)
    print("---")
    print(f"Weekly flex: {fmt_mins(weekly_flex)}")


def _print_work_blocks(blocks: List[WorkBlock]) -> None:
    projects = load_projects()
    for block in blocks:
        block_start = block.start.isoformat()[:5] if block.start is not None else ""
        block_stop = block.stop.isoformat()[:5] if block.stop is not None else ""
        if not block.stopped():
            print(f"  {block_start}-")
        else:
            print(f"  {block_start}-{block_stop} => {fmt_mins(block.worked_time)}")
        if block.comment:
            print(f"    {block.comment}")
        if block.project_id is not None:
            project = projects.get_project_by_id(block.project_id)
            print(f"    Project: {project.name}")


def fmt_mins(mins: int, expand: bool = False) -> str:
    sign = "" if mins >= 0 else "-"
    mins = abs(mins)
    if mins < 60 and not expand:
        return f"{sign}{mins}min"
    return f"{sign}{mins // 60}h {mins % 60}min"


def run():
    cfg.datafile_dir.mkdir(exist_ok=True)
    if cfg.mode == "dev":
        print("Running in dev mode.")

    ts = load_timesheet()
    print(
        f"Worked {fmt_mins(sum(d.worked_time for d in ts.days.values()), expand=True)}"
        f" of your {ts.target_hours} target hours for this month"
    )
    print(f"Monthly flex: {fmt_mins(sum(d.flex_minutes for d in ts.days.values()))} \n")
    if len(ts.today.work_blocks) > 0 and ts.today.last_work_block.is_ongoing():
        print(f"You started last work block @ {ts.today.last_work_block.start}")

    print_menu()
    done = False
    while not done:
        cmd = input("> ")
        if cmd in ("quit", "exit", "q"):
            done = True
        else:
            with contextlib.suppress(KeyError):
                handle_command(cmd.lower())


if __name__ == "__main__":
    run()
