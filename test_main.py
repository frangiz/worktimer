from os import path, remove
from typing import Any

import pytest  # type: ignore

import main
from main import Timesheet, handle_command, load_timesheet, save_timesheet


def setup_module(module: Any) -> None:
    main.DATAFILE = "test_" + main.DATAFILE


def setup_function(func: Any) -> None:
    if path.exists(main.DATAFILE):
        remove(main.DATAFILE)


def teardown_function(func: Any) -> None:
    pass


@pytest.mark.parametrize("stop_time,flex", [("16:30", 0), ("16:32", 2), ("16:27", -3)])
def test_flex(capsys, stop_time, flex) -> None:
    handle_command("start 08:00")
    handle_command("lunch")
    handle_command("stop " + stop_time)

    ts = load_timesheet()
    assert ts.today.flex_minutes == flex

    captured = capsys.readouterr()
    assert "Estimated end time for today with 30 min lunch is 16:30:00" in captured.out


def test_total_flextime() -> None:
    ts = Timesheet()
    ts.get_day("2020-07-09").flex_minutes = 38
    ts.get_day("2020-07-10").flex_minutes = 2
    ts.get_day("2020-07-11").flex_minutes = 2

    assert ts.total_flex == 42

    save_timesheet(ts)
    ts = load_timesheet()

    assert ts.total_flex == 42


# Note that no lunch was taken in this test.
def test_multiple_start_and_end(capsys) -> None:
    # Working 30 min first section
    handle_command("start 08:30")
    captured = capsys.readouterr()
    assert "Estimated end time for today with 30 min lunch is 17:00:00" in captured.out
    handle_command("stop 09:00")
    ts = load_timesheet()
    assert ts.today.flex_minutes == -7 * 60 - 30  # Should have -7h 30m as flex

    # Working 1h 30 min more
    handle_command("start 10:30")
    captured = capsys.readouterr()
    assert "Estimated end time for today with 30 min lunch is 18:30:00" in captured.out
    handle_command("stop 12:00")
    ts = load_timesheet()
    assert ts.today.flex_minutes == -6 * 60  # Should have -6h as flex

    # Filling up to the 8 hours
    handle_command("start 13:00")
    captured = capsys.readouterr()
    assert "Estimated end time for today with 30 min lunch is 19:30:00" in captured.out
    handle_command("stop 19:00")
    ts = load_timesheet()
    assert ts.today.flex_minutes == 0  # Should have 0 min as flex

    # Working a few more minutes
    handle_command("start 19:30")
    captured = capsys.readouterr()
    assert "Estimated end time for today with 30 min lunch is 20:00:00" in captured.out
    handle_command("stop 19:35")
    ts = load_timesheet()
    assert ts.today.flex_minutes == 5  # Should have 5 min as flex


# Change to be similar to stop, requiring explicit time to overwrite start?
def test_workblock_that_is_already_started_cannot_be_started_again(capsys) -> None:
    handle_command("start 08:00")
    ts = load_timesheet()

    handle_command("start 08:01")

    assert ts == load_timesheet()
    assert len(ts.today.work_blocks) == 1
    captured = capsys.readouterr()
    assert (
        "Workblock already started, stop it before starting another one" in captured.out
    )


def test_lunch_fails_if_day_is_not_started(capsys) -> None:
    handle_command("lunch")
    assert load_timesheet().today.lunch == 0
    captured = capsys.readouterr()
    assert "Could not find today in timesheet, did you start the day?" in captured.out

    handle_command("start")
    handle_command("lunch")
    assert load_timesheet().today.lunch == 30


def test_running_lunch_twice_will_not_overwrite_first_lunch() -> None:
    handle_command("start")
    handle_command("lunch")
    assert load_timesheet().today.lunch == 30
    handle_command("lunch 25")
    assert load_timesheet().today.lunch == 30


def test_stop_fails_if_last_workblock_is_not_started(capsys) -> None:
    handle_command("stop")
    assert not load_timesheet().today.last_work_block.stopped()
    captured = capsys.readouterr()
    assert "Could not stop workblock, is your last workblock started?" in captured.out

    handle_command("start 08:05")
    handle_command("stop 08:10")
    assert load_timesheet().today.last_work_block.stop == "08:10:00"
    assert load_timesheet().today.last_work_block.stopped()


def test_running_stop_twice_will_not_overwrite_last_stop() -> None:
    handle_command("start 08:01")
    handle_command("stop 08:03")
    assert load_timesheet().today.last_work_block.stop == "08:03:00"

    handle_command("stop 08:05")
    assert load_timesheet().today.last_work_block.stop == "08:03:00"
