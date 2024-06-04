import contextlib
import subprocess
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional

import click
from dotenv import dotenv_values
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table
import typer
from typing_extensions import Annotated

DEFAULT_LUNCH_DURATION = 30
console = Console()

app = typer.Typer()

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

        self.workhours_one_day = 8

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

    @property
    def worked_time(self) -> int:
        if not self.started() or not self.stopped():
            return 0
        return time_diff(self.stop, self.start)

    def started(self) -> bool:
        return self.start is not None

    def stopped(self) -> bool:
        return self.stop is not None


class Day(BaseModel):
    this_date: date
    lunch: int = 0
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
        worked_mins = sum(wt.worked_time for wt in self.work_blocks)
        return 0 if worked_mins == 0 else worked_mins - self.lunch

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


class ViewSpans(str, Enum):
    TODAY = "today"
    WEEK = "week"
    PREV_WEEK = "prev_week"
    MONTH = "month"


class RecalcAction(Enum):
    FLEX = 1


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


def handle_command(cmd: str) -> None:
    command_map = {
        "lunch": lambda params: lunch(int(params[0]))
        if params
        else lunch(DEFAULT_LUNCH_DURATION),
        "edit": lambda _: edit(),
        "view": lambda params: view(ViewSpans[params[0].upper()]) if params else view(),
        "summary": lambda _: summary(),
        "recalc": lambda params: recalc(RecalcAction[params[0].upper()])
        if params
        else recalc(),
        "timeoff": lambda params: set_time_off(int(params[0]) * 60),
        "target_hours": lambda params: set_target_hours(int(params[0])),
        "comment": lambda params: set_comment(" ".join(params) if params else None),
    }

    cmd, *params = cmd.split()

    if cmd in command_map:
        command_map[cmd](params)
    else:
        print(f"Unknown command: {cmd}")


def _print_estimated_endtime_for_today(
    work_blocks: List[WorkBlock], lunch: int = 30
) -> None:
    mins_left_to_work = (
        (cfg.workhours_one_day * 60) + lunch - sum(wt.worked_time for wt in work_blocks)
    )
    if not work_blocks[-1].start:
        return
    work_end_with_lunch = (
        (
            datetime.combine(date.today(), work_blocks[-1].start)
            + timedelta(minutes=mins_left_to_work)
        )
        .time()
        .replace(second=0, microsecond=0)
    )
    print(
        f"Estimated end time for today with {lunch} min lunch is {work_end_with_lunch}"
    )


def parse_time(time_str: str) -> datetime:
    hour, minute = time_str.split(":")
    return datetime.now().replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


@app.command()
def start(start_time: Annotated[Optional[datetime], typer.Argument(
            help="Start time in format hh:mm. If not provided, current time is used.",
            formats=["%H:%M"],
            parser=parse_time)
        ] = "00:00") -> None:
    ts = load_timesheet()
    last_wb = ts.today.last_work_block
    if last_wb.started() and not last_wb.stopped():
        print("Workblock already started, stop it before starting another one")
        return

    if last_wb.stopped():
        ts.today.work_blocks.append(WorkBlock(start=start_time.time()))
    else:
        last_wb.start = start_time.time()

    print(f"Starting at {start_time}")
    save_timesheet(ts)
    if ts.today.lunch > 0:
        _print_estimated_endtime_for_today(ts.today.work_blocks, ts.today.lunch)
    else:
        _print_estimated_endtime_for_today(ts.today.work_blocks)


@app.command()
def stop(stop_time: Annotated[Optional[datetime], typer.Argument(
            help="Stop time in format hh:mm.",
            formats=["%H:%M"],
            parser=parse_time)
        ] = "00:00",
         comment: Annotated[str, typer.Argument(
            help="Comment for the work block.",)
        ] = None) -> None:
    ts = load_timesheet()
    if not ts.today.last_work_block.started():
        print("Could not stop workblock, is your last workblock started?")
        return
    if ts.today.last_work_block.stopped():
        return
    print(f"Stopping at {stop_time}")

    ts.today.last_work_block.stop = stop_time.time()
    ts.today.last_work_block.comment = comment
    ts.today.recalc_flex()

    flex_hours = abs(ts.today.flex_minutes) // 60
    flex_mins = abs(ts.today.flex_minutes) % 60
    if ts.today.flex_minutes >= 0:
        print(f"Flex for today: {flex_hours} hours {flex_mins} mins")
    else:
        print(f"Flex for today is negative: {flex_hours} hours {flex_mins} mins")
    save_timesheet(ts)


@app.command()
def lunch(lunch_mins: Annotated[Optional[int], typer.Argument(
        help="Lunch duration in minutes. Default is 30 minutes.",
)] = 30) -> None:
    ts = load_timesheet()

    if len(ts.today.work_blocks) == 0 or not ts.today.last_work_block.started():
        print("Could not find today in timesheet, did you start the day?")
        return
    if ts.today.lunch != 0:
        return

    _print_estimated_endtime_for_today(ts.today.work_blocks, lunch_mins)

    ts.today.lunch = lunch_mins
    ts.today.recalc_flex()
    save_timesheet(ts)
    print(f"Added {lunch_mins} mins as lunch")


@app.command()
def edit() -> None:
    subprocess.call(["vim", cfg.datafile_dir.joinpath(cfg.datafile)])


@app.command()
def view(view_span: Annotated[
        Optional[ViewSpans], typer.Argument(
            case_sensitive=False
)] = ViewSpans.TODAY) -> None:
    today = datetime.now().date()
    if view_span == ViewSpans.TODAY:
        start_date = end_date = date.today()
    elif view_span == ViewSpans.WEEK:
        start_date = today - timedelta(days=today.isoweekday() - 1)
        end_date = today
    elif view_span == ViewSpans.PREV_WEEK:
        start_date = today - timedelta(days=today.isoweekday() + 6)
        end_date = start_date + timedelta(days=6)
    entries = load_timesheet().get_days(start_date.isoformat(), end_date.isoformat())

    print_days(entries)
    if view_span in [ViewSpans.WEEK, ViewSpans.PREV_WEEK]:
        _print_footer(entries)


@app.command()
def summary(viewSpan: Annotated[ViewSpans, typer.Argument(
    case_sensitive=False,
)] = ViewSpans.MONTH) -> None:
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


@app.command()
def recalc(action: Annotated[ViewSpans, typer.Argument(
    case_sensitive=False,
)] = RecalcAction.FLEX) -> None:
    if action == RecalcAction.FLEX:
        for f in cfg.datafile_dir.glob("*-timesheet.json"):
            ts = load_timesheet(f.name)
            for _, v in ts.days.items():
                v.recalc_flex()
            save_timesheet(ts, f.name)


@app.command()
def set_time_off(time_off_mins: Annotated[int, typer.Argument(
    help="Time off in minutes.",
)]) -> None:
    if time_off_mins < 0 or time_off_mins > 8 * 60:
        raise ValueError(
            "Invalid timeoff value, must be an int between 0 and 8 inclusive."
        )
    ts = load_timesheet()
    ts.today.time_off_minutes = time_off_mins
    ts.today.recalc_flex()
    save_timesheet(ts)
    print(f"Setting timeoff to {fmt_mins(time_off_mins)}")


@app.command()
def set_target_hours(target_hours: Annotated[int, typer.Argument(
    help="Target hours for the month.",
)]) -> None:
    if target_hours < 0:
        raise ValueError("Invalid target_hours value, must be an int greater than 0.")
    ts = load_timesheet()
    ts.target_hours = target_hours
    save_timesheet(ts)
    print(f"Setting target hours to {target_hours}")


@app.command()
def set_comment(text: Annotated[Optional[str], typer.Argument(
    help="Comment for the last work block. If empty, the previous comment is removed.",
)]) -> None:
    ts = load_timesheet()
    if ts.today.last_work_block.started() and not ts.today.last_work_block.stopped():
        ts.today.last_work_block.comment = text
    else:
        print("Cannot set comment for workblock not started")
    save_timesheet(ts)


def calc_total_flex() -> int:
    return sum(
        load_timesheet(f.name).monthly_flex
        for f in cfg.datafile_dir.glob("*-timesheet.json")
    )


def total_flex_as_str() -> str:
    return fmt_mins(calc_total_flex())


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
    for block in blocks:
        block_start = block.start.isoformat()[:5] if block.start is not None else ""
        block_stop = block.stop.isoformat()[:5] if block.stop is not None else ""
        if not block.stopped():
            print(f"  {block_start}-")
        else:
            print(f"  {block_start}-{block_stop} => {fmt_mins(block.worked_time)}")
            if block.comment:
                print(f"    {block.comment}")


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
    if len(ts.today.work_blocks) > 0:
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
    app()
