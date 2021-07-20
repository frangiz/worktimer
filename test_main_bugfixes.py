from pathlib import Path
from typing import Any

from freezegun import freeze_time  # type: ignore

import main
from main import handle_command, load_timesheet


def setup_module(module: Any) -> None:
    main.cfg.datafile_dir = Path("test_files")
    main.cfg.datafile_dir.mkdir(exist_ok=True)


def setup_function(func: Any) -> None:
    files = main.cfg.datafile_dir.glob("*-timesheet.json")
    for f in files:
        f.unlink()


def teardown_function(func: Any) -> None:
    pass


def test_flex_for_today_output_is_wrong(capsys) -> None:
    with freeze_time("2020-09-22"):  # A Tuesday
        handle_command("start 08:12")
        handle_command("stop 16:05")
        captured = capsys.readouterr()
        assert "Flex for today is negative: 0 hours 7 mins" in captured.out

        ts = load_timesheet()
        assert ts.today.flex_minutes == -7


def test_two_workblocks_20_min_lunch_estimated_endtime_is_wrong(capsys) -> None:
    with freeze_time("2020-11-20"):  # A Friday
        handle_command("start 07:45")
        handle_command("lunch 20")
        handle_command("stop 13:54")  # 5h 49 min -> -2h 11 min flex
        captured = capsys.readouterr()
        assert "Flex for today is negative: 2 hours 11 mins" in captured.out
        ts = load_timesheet()
        assert ts.today.flex_minutes == -(2 * 60 + 11)

        # workblock #2
        handle_command("start 15:00")
        handle_command("stop 17:00")
        captured = capsys.readouterr()

        # This assert asserts the testcase
        assert (
            "Estimated end time for today with 20 min lunch is 17:11:00" in captured.out
        )
        assert "Flex for today is negative: 0 hours 11 mins" in captured.out
        ts = load_timesheet()
        assert ts.today.flex_minutes == -11
