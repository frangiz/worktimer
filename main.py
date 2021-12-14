import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Dict, List, Union

from dataclasses_json import dataclass_json
from dotenv import dotenv_values


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
            f"{datetime.today().year}-{datetime.today().month:02d}-timesheet.json"
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


@dataclass_json
@dataclass
class WorkBlock:
    start: str = ""
    stop: str = ""

    @property
    def worked_time(self) -> int:
        if not self.started() or not self.stopped():
            return 0
        diff = _today_with_time(self.stop) - _today_with_time(self.start)
        return int(diff.total_seconds()) // 60

    def started(self) -> bool:
        return self.start != ""

    def stopped(self) -> bool:
        return self.stop != ""


@dataclass_json
@dataclass
class Day:
    date_str: str = ""
    lunch: int = 0
    flex_minutes: int = 0
    work_blocks: List[WorkBlock] = field(default_factory=lambda: [])
    time_off_minutes: int = 0

    @property
    def last_work_block(self) -> WorkBlock:
        if len(self.work_blocks) == 0:
            self.work_blocks.append(WorkBlock())
        return self.work_blocks[-1]

    def recalc_flex(self) -> None:
        expected_worktime_in_mins = cfg.workhours_one_day * 60
        time_off_minutes = self.time_off_minutes
        weekday = datetime.strptime(self.date_str, "%Y-%m-%d").isoweekday()
        # Check if weekend
        if weekday in [6, 7]:
            expected_worktime_in_mins = 0
            time_off_minutes = 0
        if len(self.work_blocks) == 1 and not self.last_work_block.stopped():
            self.flex_minutes = 0
        else:
            self.flex_minutes = (
                sum(wt.worked_time for wt in self.work_blocks)
                - expected_worktime_in_mins
                - self.lunch
                + time_off_minutes
            )

    @staticmethod
    def from_date_str(date_str: str) -> "Day":
        d = Day()
        d.date_str = date_str
        return d


@dataclass_json
@dataclass
class Timesheet:
    days: Dict[str, Day] = field(default_factory=lambda: {})

    @property
    def monthly_flex(self) -> int:
        return sum(d.flex_minutes for d in self.days.values())

    @property
    def today(self) -> Day:
        today = _today_iso_format()
        if today not in self.days:
            self.days[today] = Day.from_date_str(today)
        return self.days[today]

    def get_day(self, key: str) -> Day:
        if key not in self.days:
            self.days[key] = Day.from_date_str(key)
        return self.days[key]

    def to_dict(self) -> Dict:
        return {}


class ViewSpans(Enum):
    TODAY = auto()
    WEEK = auto()


class RecalcAction(Enum):
    FLEX = 1


def load_timesheet(datafile: str = None) -> Timesheet:
    if datafile is None:
        datafile = cfg.datafile
    if not cfg.datafile_dir.joinpath(datafile).is_file():
        empty_ts = Timesheet()
        save_timesheet(empty_ts)
    with open(cfg.datafile_dir.joinpath(datafile), "r", encoding="utf-8") as f:
        ts = Timesheet.from_dict(json.load(f))  # type: ignore
        for k, v in ts.days.items():
            v.date_str = k
        return ts


def save_timesheet(ts: Timesheet, datafile: str = None) -> None:
    if datafile is None:
        datafile = cfg.datafile
    with open(cfg.datafile_dir.joinpath(datafile), "w+", encoding="utf-8") as f:
        json.dump(ts.to_dict(), f, ensure_ascii=False, indent=4, sort_keys=True)


def _time_cmd(cmd: Callable[[datetime], None], params: List[str]) -> None:
    if params:
        h, m = map(int, params[0].split(":"))
        time = datetime.today().replace(hour=h, minute=m, second=0, microsecond=0)
        cmd(time)
    else:
        cmd(datetime.now().replace(second=0, microsecond=0))


def handle_command(cmd: str) -> None:
    cmd, *params = cmd.split()
    if cmd == "start":
        _time_cmd(start, params)
    elif cmd == "stop":
        _time_cmd(stop, params)
    elif cmd == "lunch":
        if len(params) > 0:
            lunch(int(params[0]))
        else:
            lunch(30)
    elif cmd == "edit":
        edit()
    elif cmd == "view":
        if len(params) > 0:
            view(ViewSpans[params[0].upper()])
        else:
            view()
    elif cmd == "recalc":
        if len(params) > 0:
            recalc(RecalcAction[params[0].upper()])
        else:
            recalc()
    elif cmd == "timeoff":
        if len(params) > 0:
            set_time_off(int(params[0]) * 60)
    elif cmd == "help":
        print("help you say?")


def _today_with_time(time: Union[str, time]) -> datetime:
    if type(time) is str:
        return datetime.strptime(
            date.today().isoformat() + "T" + str(time), "%Y-%m-%dT%H:%M:%S"
        )
    raise Exception("Not implemented yet")


def _print_estimated_endtime_for_today(
    work_blocks: List[WorkBlock], lunch: int = 30
) -> None:
    mins_left_to_work = (
        (cfg.workhours_one_day * 60) + lunch - sum(wt.worked_time for wt in work_blocks)
    )
    work_end_with_lunch = (
        (_today_with_time(work_blocks[-1].start) + timedelta(minutes=mins_left_to_work))
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
        ts.today.work_blocks.append(WorkBlock(start=start_time.time().isoformat()))
    else:
        last_wb.start = start_time.time().isoformat()

    print(f"Starting at {start_time}")
    save_timesheet(ts)
    if ts.today.lunch > 0:
        _print_estimated_endtime_for_today(ts.today.work_blocks, ts.today.lunch)
    else:
        _print_estimated_endtime_for_today(ts.today.work_blocks)


def stop(stop_time: datetime) -> None:
    ts = load_timesheet()
    if not ts.today.last_work_block.started():
        print("Could not stop workblock, is your last workblock started?")
        return
    if ts.today.last_work_block.stopped():
        return
    print(f"Stopping at {stop_time}")

    ts.today.last_work_block.stop = stop_time.time().isoformat()
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
    if viewSpan == ViewSpans.TODAY:
        print_days([ts.today])
    elif viewSpan == ViewSpans.WEEK:
        days_to_show = []
        _, _, weekday = date.today().isocalendar()
        for n in range(weekday - 1, -1, -1):
            days_to_show.append(
                ts.get_day((date.today() - timedelta(days=n)).isoformat())
            )
        print_days(days_to_show)


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
    print("view [TODAY]")
    print("recalc [FLEX]")
    print("timeoff [hours]")


def print_days(days: List[Day]) -> None:
    for day in days:
        header = " | ".join(
            [
                day.date_str,
                f"lunch: {fmt_mins(day.lunch)}",
                f"daily flex: {fmt_mins(day.flex_minutes)}",
            ]
        )
        print(header)
        _print_work_blocks(day.work_blocks)


def _print_work_blocks(blocks: List[WorkBlock]) -> None:
    for block in blocks:
        if not block.stopped():
            print(f"  {block.start[:5]}-")
        else:
            print(
                f"  {block.start[:5]}-{block.stop[:5]} => {fmt_mins(block.worked_time)}"
            )


def fmt_mins(mins: int) -> str:
    sign = "" if mins >= 0 else "-"
    mins = abs(mins)
    if mins < 60:
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
            handle_command(cmd.lower())


if __name__ == "__main__":
    run()
