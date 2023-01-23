import contextlib
import subprocess
from datetime import date, datetime, time, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import dotenv_values
from pydantic import BaseModel, Field


class Config:
    mode: str
    datafile_dir: Path
    datafile: str
    workhours_one_day: int
    notepadpp_path: str

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
        self.notepadpp_path = r"C:\Program Files (x86)\Notepad++\notepad++.exe"

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
    start: Optional[time]
    stop: Optional[time]
    comment: Optional[str]

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


class ViewSpans(Enum):
    TODAY = auto()
    WEEK = auto()
    MONTH = auto()


class RecalcAction(Enum):
    FLEX = 1


def load_timesheet(datafile: Optional[str] = None) -> Timesheet:
    if datafile is None:
        datafile = cfg.datafile
    if not cfg.datafile_dir.joinpath(datafile).is_file():
        empty_ts = Timesheet()
        save_timesheet(empty_ts)
    return Timesheet.parse_file(cfg.datafile_dir.joinpath(datafile))


def save_timesheet(ts: Timesheet, datafile: Optional[str] = None) -> None:
    if datafile is None:
        datafile = cfg.datafile
    with open(cfg.datafile_dir.joinpath(datafile), "w+", encoding="utf-8") as f:
        f.write(ts.json(ensure_ascii=False, indent=4, sort_keys=True))


def handle_command(cmd: str) -> None:
    cmd, *params = cmd.split()
    if cmd == "start":
        if params:
            h, m = map(int, params[0].split(":"))
            time = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
            start(time)
        else:
            start(datetime.now().replace(second=0, microsecond=0))
    elif cmd == "stop":
        if params:
            h, m = map(int, params[0].split(":"))
            time = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
            comment = " ".join(params[1:]) if len(params) > 1 else None
            stop(time, comment)
        else:
            stop(datetime.now().replace(second=0, microsecond=0))
    elif cmd == "lunch":
        if params:
            lunch(int(params[0]))
        else:
            lunch(30)
    elif cmd == "edit":
        edit()
    elif cmd == "view":
        if params:
            view(ViewSpans[params[0].upper()])
        else:
            view()
    elif cmd == "summary":
        summary()
    elif cmd == "recalc":
        if params:
            recalc(RecalcAction[params[0].upper()])
        else:
            recalc()
    elif cmd == "timeoff":
        if params:
            set_time_off(int(params[0]) * 60)
    elif cmd == "help":
        print("help you say?")


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


def start(start_time: datetime) -> None:
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


def stop(stop_time: datetime, comment: Optional[str] = None) -> None:
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


def lunch(lunch_mins: int) -> None:
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


def edit() -> None:
    subprocess.call([cfg.notepadpp_path, cfg.datafile_dir.joinpath(cfg.datafile)])


def view(viewSpan: ViewSpans = ViewSpans.TODAY) -> None:
    ts = load_timesheet()
    days_to_show = []
    if viewSpan == ViewSpans.TODAY:
        days_to_show.append(ts.today)
    elif viewSpan == ViewSpans.WEEK:
        _, _, weekday = date.today().isocalendar()
        for n in range(weekday - 1, -1, -1):
            days_to_show.append(
                ts.get_day((date.today() - timedelta(days=n)).isoformat())
            )
    print_days(days_to_show)
    if viewSpan == ViewSpans.WEEK:
        _print_footer(days_to_show)


def summary(viewSpan: ViewSpans = ViewSpans.MONTH) -> None:
    ts = load_timesheet()
    days: List[Day] = []
    for n in range(date.today().day - 1, -1, -1):
        days.append(ts.get_day((date.today() - timedelta(days=n)).isoformat()))
    print("date       | worked time | daily flex |")
    for d in days:
        the_date = d.this_date.isoformat()
        worked_time = (
            fmt_mins(d.worked_time, expand=True) if d.worked_time > 0 else "---"
        )
        daily_flex = fmt_mins(d.flex_minutes) if d.worked_time > 0 else "---"
        print(f"{the_date:<11}| {worked_time:<12}| {daily_flex:<11}|")
    print("---")
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


def recalc(action: RecalcAction = RecalcAction.FLEX) -> None:
    if action == RecalcAction.FLEX:
        for f in cfg.datafile_dir.glob("*-timesheet.json"):
            ts = load_timesheet(f.name)
            for _, v in ts.days.items():
                v.recalc_flex()
            save_timesheet(ts, f.name)


def set_time_off(time_off_mins: int) -> None:
    if time_off_mins < 0 or time_off_mins > 8 * 60:
        raise ValueError(
            "Invalid timeoff value, must be an int between 0 and 8 inclusive."
        )
    ts = load_timesheet()
    ts.today.time_off_minutes = time_off_mins
    ts.today.recalc_flex()
    save_timesheet(ts)
    print(f"Setting timeoff to {fmt_mins(time_off_mins)}")


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
    print("lunch [n]")
    print("edit")
    print("view [TODAY|WEEK]")
    print("summary [MONTH]")
    print("recalc [FLEX]")
    print("timeoff [hours]")


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
    print(f"Total flex {total_flex_as_str()}")
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
    run()
