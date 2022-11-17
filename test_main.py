from datetime import time
from pathlib import Path
from typing import Any

import pytest  # type: ignore
from freezegun import freeze_time  # type: ignore

import main
from main import (
    Timesheet,
    calc_total_flex,
    fmt_mins,
    handle_command,
    load_timesheet,
    save_timesheet,
    total_flex_as_str,
)


def setup_module(module: Any) -> None:
    main.cfg.datafile_dir = Path("test_files")
    main.cfg.datafile_dir.mkdir(exist_ok=True)


def setup_function(func: Any) -> None:
    files = main.cfg.datafile_dir.glob("*-timesheet.json")
    for f in files:
        f.unlink()


def teardown_function(func: Any) -> None:
    pass


@pytest.mark.parametrize("stop_time,flex", [("16:30", 0), ("16:32", 2), ("16:27", -3)])
def test_flex(capsys, stop_time, flex) -> None:
    with freeze_time("2020-09-23"):  # A Wednesday
        handle_command("start 08:00")
        handle_command("lunch")
        handle_command(f"stop {stop_time}")

        ts = load_timesheet()
        assert ts.today.flex_minutes == flex

        captured = capsys.readouterr()
        assert (
            "Estimated end time for today with 30 min lunch is 16:30:00" in captured.out
        )


def test_monthly_flextime() -> None:
    ts = Timesheet()
    ts.get_day("2020-07-09").flex_minutes = 38
    ts.get_day("2020-07-10").flex_minutes = 2
    ts.get_day("2020-07-11").flex_minutes = 2

    assert ts.monthly_flex == 42

    save_timesheet(ts)
    ts = load_timesheet()

    assert ts.monthly_flex == 42


# Note that no lunch was taken in this test.
def test_multiple_start_and_end(capsys) -> None:
    with freeze_time("2020-09-25"):  # A Friday
        # Working 30 min first section
        handle_command("start 08:30")
        captured = capsys.readouterr()
        assert (
            "Estimated end time for today with 30 min lunch is 17:00:00" in captured.out
        )
        handle_command("stop 09:00")
        ts = load_timesheet()
        assert ts.today.flex_minutes == -7 * 60 - 30  # Should have -7h 30m as flex

        # Working 1h 30 min more
        handle_command("start 10:30")
        captured = capsys.readouterr()
        assert (
            "Estimated end time for today with 30 min lunch is 18:30:00" in captured.out
        )
        handle_command("stop 12:00")
        ts = load_timesheet()
        assert ts.today.flex_minutes == -6 * 60  # Should have -6h as flex

        # Filling up to the 8 hours
        handle_command("start 13:00")
        captured = capsys.readouterr()
        assert (
            "Estimated end time for today with 30 min lunch is 19:30:00" in captured.out
        )
        handle_command("stop 19:00")
        ts = load_timesheet()
        assert ts.today.flex_minutes == 0  # Should have 0 min as flex

        # Working a few more minutes
        handle_command("start 19:30")
        captured = capsys.readouterr()
        assert (
            "Estimated end time for today with 30 min lunch is 20:00:00" in captured.out
        )
        handle_command("stop 19:35")
        ts = load_timesheet()
        assert ts.today.flex_minutes == 5  # Should have 5 min as flex


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
    assert load_timesheet().today.last_work_block.stop == time.fromisoformat("08:10:00")
    assert load_timesheet().today.last_work_block.stopped()


def test_running_stop_twice_will_not_overwrite_last_stop() -> None:
    handle_command("start 08:01")
    handle_command("stop 08:03")
    assert load_timesheet().today.last_work_block.stop == time.fromisoformat("08:03:00")

    handle_command("stop 08:05")
    assert load_timesheet().today.last_work_block.stop == time.fromisoformat("08:03:00")


def test_total_flextime() -> None:
    ts = Timesheet()
    ts.get_day("2020-07-01").flex_minutes = 2
    main.cfg.datafile = "2020-07-timesheet.json"
    save_timesheet(ts)

    ts = Timesheet()
    ts.get_day("2020-08-01").flex_minutes = 3
    main.cfg.datafile = "2020-08-timesheet.json"
    save_timesheet(ts)

    assert calc_total_flex() == 5


def test_flextime_correct_during_weekend() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-26"):  # A Saturday
        handle_command("start 08:00")
        handle_command("stop 09:02")

        assert load_timesheet().today.flex_minutes == 62
        assert calc_total_flex() == 62


def test_total_flex_as_str_less_than_one_hour() -> None:
    ts = Timesheet()
    ts.get_day("2020-07-01").flex_minutes = 2
    main.cfg.datafile = "2020-07-timesheet.json"
    save_timesheet(ts)

    assert total_flex_as_str() == "2min"


def test_total_flex_as_str_exactly_one_hour() -> None:
    ts = Timesheet()
    ts.get_day("2020-07-01").flex_minutes = 60
    main.cfg.datafile = "2020-07-timesheet.json"
    save_timesheet(ts)

    assert total_flex_as_str() == "1h 0min"


def test_total_flex_as_str_more_than_one_hour() -> None:
    ts = Timesheet()
    ts.get_day("2020-07-01").flex_minutes = 63
    main.cfg.datafile = "2020-07-timesheet.json"
    save_timesheet(ts)

    assert total_flex_as_str() == "1h 3min"


def test_start_no_arguments() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-26 08:03"):  # A Saturday
        handle_command("start")

        assert load_timesheet().today.last_work_block.start == time.fromisoformat(
            "08:03:00"
        )


def test_stop_no_arguments() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-26 08:07"):  # A Saturday
        handle_command("start 08:00")
        handle_command("stop")

        assert load_timesheet().today.last_work_block.stop == time.fromisoformat(
            "08:07:00"
        )


def test_view_today(capsys) -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with freeze_time("2020-11-24"):  # A Tuesday
        handle_command("start 08:02")
        handle_command("lunch 25")
        handle_command("stop 14:21")
        handle_command("start 15:01")
        handle_command("stop 17:27")
        capsys.readouterr()

        handle_command("view")  # Act
    captured = capsys.readouterr()

    expected = [
        "2020-11-24 | worked time: 8h 20min | lunch: 25min | daily flex: 20min",
        "  08:02-14:21 => 6h 19min",
        "  15:01-17:27 => 2h 26min",
    ]
    assert "\n".join(expected) in captured.out


def test_view_today_with_workblock_not_ended(capsys) -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with freeze_time("2020-11-24"):  # A Tuesday
        handle_command("start 08:02")
        capsys.readouterr()

        handle_command("view")  # Act
    captured = capsys.readouterr()

    expected = [
        "2020-11-24 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "  08:02-",
    ]
    assert "\n".join(expected) in captured.out


def test_view_week(capsys) -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with freeze_time("2020-11-22"):  # A Sunday the week before
        handle_command("start 08:00")
        handle_command("lunch")
        handle_command("stop 16:30")
    # The Monday is intentionally excluded
    with freeze_time("2020-11-24"):  # A Tuesday
        handle_command("start 08:02")
        handle_command("lunch")
        handle_command("stop 16:30")

    with freeze_time("2020-11-25"):  # A Wednesday
        handle_command("start 08:02")
        handle_command("lunch 25")
        handle_command("stop 14:21")
        handle_command("start 15:01")
        handle_command("stop 17:27")
        capsys.readouterr()

        handle_command("view week")  # Act
    captured = capsys.readouterr()

    expected = [
        "2020-11-23 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "",
        "2020-11-24 | worked time: 7h 58min | lunch: 30min | daily flex: -2min",
        "  08:02-16:30 => 8h 28min",
        "",
        "2020-11-25 | worked time: 8h 20min | lunch: 25min | daily flex: 20min",
        "  08:02-14:21 => 6h 19min",
        "  15:01-17:27 => 2h 26min",
    ]
    assert "\n".join(expected) in captured.out


def test_view_is_case_insensitive(capsys) -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with freeze_time("2020-11-24"):  # A Tuesday
        handle_command("start 08:02")
        capsys.readouterr()

        handle_command("view ToDaY")  # Act
    captured = capsys.readouterr()

    expected = [
        "2020-11-24 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "  08:02-",
    ]
    assert "\n".join(expected) in captured.out


def test_timeoff_half_day() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with freeze_time("2021-04-02"):  # A Friday
        handle_command("timeoff 4")
        handle_command("start 08:00")
        handle_command("stop 12:02")

        ts = load_timesheet()
        assert ts.today.time_off_minutes == 4 * 60
        assert ts.today.flex_minutes == 2


def test_timeoff_full_day() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with freeze_time("2021-04-02"):  # A Friday
        handle_command("timeoff 8")

        ts = load_timesheet()
        assert ts.today.time_off_minutes == 8 * 60
        assert ts.today.flex_minutes == 0


def test_timeoff_recalcs_flex() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with freeze_time("2021-04-02"):  # A Friday
        handle_command("start 08:00")
        handle_command("stop 12:02")

        ts = load_timesheet()
        assert ts.today.time_off_minutes == 0
        assert ts.today.flex_minutes == -4 * 60 + 2

        handle_command("timeoff 4")
        ts = load_timesheet()
        assert ts.today.time_off_minutes == 4 * 60
        assert ts.today.flex_minutes == 2


def test_timeoff_negative_input() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with freeze_time("2021-04-02"):  # A Friday
        with pytest.raises(
            ValueError,
            match="Invalid timeoff value, must be an int between 0 and 8 inclusive.",
        ):
            handle_command("timeoff -1")


def test_timeoff_more_than_a_workday() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with freeze_time("2021-04-02"):  # A Friday
        with pytest.raises(
            ValueError,
            match="Invalid timeoff value, must be an int between 0 and 8 inclusive.",
        ):
            handle_command("timeoff 9")


@pytest.mark.parametrize(
    "mins,expected",
    [(50, "50min"), (70, "1h 10min"), (-20, "-20min"), (-70, "-1h 10min")],
)
def test_fmt_mins(mins, expected) -> None:
    assert fmt_mins(mins) == expected


def test_worked_time() -> None:
    handle_command("start 08:00")
    handle_command("stop 09:00")  # Worked 1 hour

    handle_command("start 12:00")
    handle_command("stop 14:00")  # Worked 2 more hours

    ts = load_timesheet()
    assert ts.today.worked_time == 3 * 60


def test_worked_time_with_lunch() -> None:
    handle_command("start 08:00")
    handle_command("stop 09:00")
    handle_command("lunch 25")

    ts = load_timesheet()
    # Worked 35 min and had 25 min lunch
    assert ts.today.worked_time == 35
    assert ts.today.lunch == 25


def test_worked_time_with_no_block_stopped() -> None:
    handle_command("start 08:10")
    handle_command("lunch")

    ts = load_timesheet()
    assert ts.today.worked_time == 0


def test_worked_time_with_a_block_not_stopped() -> None:
    handle_command("start 08:10")
    handle_command("stop 08:30")  # Worked 2 mins
    handle_command("start 08:50")

    ts = load_timesheet()
    assert ts.today.worked_time == 20
