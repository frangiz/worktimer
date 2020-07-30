import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Dict, List, Union

from dataclasses_json import dataclass_json

WORKHOURS_ONE_DAY = 8
NOTEPADPP_PATH = r"C:\Program Files (x86)\Notepad++\notepad++.exe"

DATAFILE = f"{datetime.today().year}-{datetime.today().month:02d}-timesheet.json"


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
    lunch: int = 0
    flex_minutes: int = 0
    work_blocks: List[WorkBlock] = field(default_factory=lambda: [])

    @property
    def last_work_block(self) -> WorkBlock:
        if len(self.work_blocks) == 0:
            self.work_blocks.append(WorkBlock())
        return self.work_blocks[-1]

    def recalc_flex(self) -> None:
        self.flex_minutes = (
            sum(wt.worked_time for wt in self.work_blocks)
            - (WORKHOURS_ONE_DAY * 60)
            - self.lunch
        )


@dataclass_json
@dataclass
class Timesheet:
    days: Dict[str, Day] = field(default_factory=lambda: {})

    @property
    def total_flex(self) -> int:
        return sum(d.flex_minutes for d in self.days.values())

    @property
    def today(self) -> Day:
        today = _today_iso_format()
        if today not in self.days:
            self.days[today] = Day()
        return self.days[today]

    def get_day(self, key: str) -> Day:
        if key not in self.days:
            self.days[key] = Day()
        return self.days[key]


class ViewSpans(Enum):
    TODAY = 1


def load_timesheet() -> Timesheet:
    if not os.path.exists(DATAFILE):
        empty_ts = Timesheet()
        save_timesheet(empty_ts)
    with open(DATAFILE, "r", encoding="utf-8") as f:
        return Timesheet.from_dict(json.load(f))  # type: ignore


def save_timesheet(ts: Timesheet) -> None:
    with open(DATAFILE, "w+", encoding="utf-8") as f:
        json.dump(ts.to_dict(), f, ensure_ascii=False, indent=4, sort_keys=True)  # type: ignore


def handle_command(cmd: str) -> None:
    cmd, *params = cmd.split()
    if cmd == "start":
        if len(params) > 0:
            h, m = params[0].split(":")
            start_time = datetime.today().replace(
                hour=int(h), minute=int(m), second=0, microsecond=0
            )
            start(start_time)
        else:
            start(_get_start_time())
    elif cmd == "stop":
        if len(params) > 0:
            h, m = params[0].split(":")
            stop_time = datetime.today().replace(
                hour=int(h), minute=int(m), second=0, microsecond=0
            )
            stop(stop_time)
        else:
            stop(_get_stop_time())
    elif cmd == "lunch":
        if len(params) > 0:
            lunch(int(params[0]))
        else:
            lunch(30)
    elif cmd == "edit":
        edit()
    elif cmd == "view":
        if len(params) > 0:
            view(ViewSpans[params[0]])
        else:
            view()
    elif cmd == "help":
        print("help you say?")


def _get_start_time() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _get_stop_time() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _today_with_time(time: Union[str, time]) -> datetime:
    if type(time) is str:
        return datetime.strptime(
            date.today().isoformat() + "T" + str(time), "%Y-%m-%dT%H:%M:%S"
        )
    raise Exception("Not implemented yet")


def _today_iso_format() -> str:
    return date.today().isoformat()


def _print_estimated_endtime_for_today(
    work_blocks: List[WorkBlock], lunch: int = 30
) -> None:
    mins_left_to_work = (
        (WORKHOURS_ONE_DAY * 60) + lunch - sum(wt.worked_time for wt in work_blocks)
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
    print(
        f"Flex for today: {ts.today.flex_minutes // 60 } hours {ts.today.flex_minutes % 60} mins"
    )
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
    subprocess.call([NOTEPADPP_PATH, DATAFILE])


def view(viewSpan: ViewSpans = ViewSpans.TODAY) -> None:
    if viewSpan == ViewSpans.TODAY:
        ts = load_timesheet()
        print(ts.today)


def print_menu():
    print("-- comamnds --")
    print("start [hh:mm]")
    print("stop [hh:mm]")
    print("lunch [n]")
    print("edit")
    print("view [TODAY]")


def run():
    ts = load_timesheet()
    print(f"Current flex {ts.total_flex} mins")
    if len(ts.today.work_blocks) > 0:
        print(f"You started last work block @ {ts.today.last_work_block.start}")
        _print_estimated_endtime_for_today(ts.today.work_blocks, 30)

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
