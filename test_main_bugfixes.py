from pathlib import Path
from typing import Any

from freezegun import freeze_time  # type: ignore

import main
from main import handle_command, load_timesheet


def setup_module(module: Any) -> None:
    main.DATAFILE_DIR = Path("test_files")
    main.DATAFILE_DIR.mkdir(exist_ok=True)


def setup_function(func: Any) -> None:
    files = main.DATAFILE_DIR.glob("*-timesheet.json")
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
