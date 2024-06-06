from datetime import time
from pathlib import Path
from typing import Any, List

import pytest  # type: ignore
import time_machine
from typer.testing import CliRunner  # type: ignore

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

runner = CliRunner()

def setup_module(module: Any) -> None:
    main.cfg.datafile_dir = Path("test_files")
    main.cfg.datafile_dir.mkdir(exist_ok=True)


def setup_function(func: Any) -> None:
    files = main.cfg.datafile_dir.glob("*-timesheet.json")
    for f in files:
        f.unlink()
    captured_output = Path(main.cfg.datafile_dir, "captured_output.txt")
    if captured_output.exists():
        captured_output.unlink()


def teardown_function(func: Any) -> None:
    pass


def write_captured_output(captured_output: str) -> None:
    Path(main.cfg.datafile_dir, "captured_output.txt").write_text(captured_output)


def assert_captured_out_starts_with(expected: List[str], result: str) -> None:
    assert expected == result.split("\n")[: len(expected)]


@pytest.mark.parametrize("stop_time,flex", [("16:30", 0), ("16:32", 2), ("16:27", -3)])
def test_flex(stop_time, flex) -> None:
    with time_machine.travel("2020-09-23"):  # A Wednesday
        result = runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["lunch"])
        runner.invoke(main.app, ["stop", f"{stop_time}"])

        ts = load_timesheet()
        assert ts.today.flex_minutes == flex

        assert (
            "Estimated end time for today with 30 min lunch is 16:30:00" in result.stdout
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
def test_multiple_start_and_end() -> None:
    with time_machine.travel("2020-09-25"):  # A Friday
        # Working 30 min first section
        result = runner.invoke(main.app, ["start", "08:30"])
        assert (
            "Estimated end time for today with 30 min lunch is 17:00:00" in result.stdout
        )
        runner.invoke(main.app, ["stop", "09:00"])
        ts = load_timesheet()
        assert ts.today.flex_minutes == -7 * 60 - 30  # Should have -7h 30m as flex

        # Working 1h 30 min more
        result = runner.invoke(main.app, ["start", "10:30"])
        assert (
            "Estimated end time for today with 30 min lunch is 18:30:00" in result.stdout
        )
        runner.invoke(main.app, ["stop", "12:00"])
        ts = load_timesheet()
        assert ts.today.flex_minutes == -6 * 60  # Should have -6h as flex

        # Filling up to the 8 hours
        result = runner.invoke(main.app, ["start", "13:00"])
        assert (
            "Estimated end time for today with 30 min lunch is 19:30:00" in result.stdout
        )
        runner.invoke(main.app, ["stop", "19:00"])
        ts = load_timesheet()
        assert ts.today.flex_minutes == 0  # Should have 0 min as flex

        # Working a few more minutes
        result = runner.invoke(main.app, ["start", "19:30"])
        assert (
            "Estimated end time for today with 30 min lunch is 20:00:00" in result.stdout
        )
        runner.invoke(main.app, ["stop", "19:35"])
        ts = load_timesheet()
        assert ts.today.flex_minutes == 5  # Should have 5 min as flex


def test_workblock_that_is_already_started_cannot_be_started_again() -> None:
    runner.invoke(main.app, ["start", "08:00"])
    ts = load_timesheet()

    result = runner.invoke(main.app, ["start", "08:01"])

    assert ts == load_timesheet()
    assert len(ts.today.work_blocks) == 1
    assert (
        "Workblock already started, stop it before starting another one" in result.stdout
    )


def test_lunch_fails_if_day_is_not_started() -> None:
    result = runner.invoke(main.app, ["lunch"])
    assert load_timesheet().today.lunch == 0
    assert "Could not find today in timesheet, did you start the day?" in result.stdout

    runner.invoke(main.app, ["start"])
    runner.invoke(main.app, ["lunch"])
    assert load_timesheet().today.lunch == 30


def test_running_lunch_twice_will_not_overwrite_first_lunch() -> None:
    runner.invoke(main.app, ["start"])
    runner.invoke(main.app, ["lunch"])
    assert load_timesheet().today.lunch == 30
    runner.invoke(main.app, ["lunch", "25"])
    assert load_timesheet().today.lunch == 30


def test_stop_fails_if_last_workblock_is_not_started() -> None:
    result = runner.invoke(main.app, ["stop"])
    assert not load_timesheet().today.last_work_block.stopped()
    assert "Could not stop workblock, is your last workblock started?" in result.stdout

    runner.invoke(main.app, ["start", "08:05"])
    runner.invoke(main.app, ["stop", "08:10"])
    assert load_timesheet().today.last_work_block.stop == time.fromisoformat("08:10:00")
    assert load_timesheet().today.last_work_block.stopped()


def test_running_stop_twice_will_not_overwrite_last_stop() -> None:
    runner.invoke(main.app, ["start", "08:01"])
    runner.invoke(main.app, ["stop", "08:03"])
    assert load_timesheet().today.last_work_block.stop == time.fromisoformat("08:03:00")

    runner.invoke(main.app, ["stop", "08:05"])
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
    with time_machine.travel("2020-09-26"):  # A Saturday
        runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["stop", "09:02"])

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
    with time_machine.travel("2020-09-26 08:03"):  # A Saturday
        runner.invoke(main.app, ["start"])

        assert load_timesheet().today.last_work_block.start == time.fromisoformat(
            "08:03:00"
        )


def test_stop_no_arguments() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with time_machine.travel("2020-09-26 08:07"):  # A Saturday
        runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["stop"])

        assert load_timesheet().today.last_work_block.stop == time.fromisoformat(
            "08:07:00"
        )


def test_view_today() -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with time_machine.travel("2020-11-24"):  # A Tuesday
        runner.invoke(main.app, ["start", "08:02"])
        runner.invoke(main.app, ["lunch", "25"])
        runner.invoke(main.app, ["stop", "14:21"])
        runner.invoke(main.app, ["start", "15:01"])
        runner.invoke(main.app, ["stop", "17:27"])

        result = runner.invoke(main.app, ["view"])  # Act

    expected = [
        "2020-11-24 | worked time: 8h 20min | lunch: 25min | daily flex: 20min",
        "  08:02-14:21 => 6h 19min",
        "  15:01-17:27 => 2h 26min",
    ]
    assert_captured_out_starts_with(expected, result.stdout)


def test_view_today_with_workblock_not_ended() -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with time_machine.travel("2020-11-24"):  # A Tuesday
        runner.invoke(main.app, ["start", "08:02"])

        result = runner.invoke(main.app, ["view"])  # Act

    expected = [
        "2020-11-24 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "  08:02-",
    ]
    assert_captured_out_starts_with(expected, result.stdout)


def test_view_today_with_a_comment() -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with time_machine.travel("2020-11-24"):  # A Tuesday
        runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["stop", "09:00", "Worked on solving the crazy hard bug."])
        runner.invoke(main.app, ["start", "10:00"])
        runner.invoke(main.app, ["stop", "11:30"])

        result = runner.invoke(main.app, ["view"])  # Act

    expected = [
        "2020-11-24 | worked time: 2h 30min | lunch: 0min | daily flex: -5h 30min",
        "  08:00-09:00 => 1h 0min",
        "    Worked on solving the crazy hard bug.",
        "  10:00-11:30 => 1h 30min",
    ]
    assert_captured_out_starts_with(expected, result.stdout)


def test_view_week() -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with time_machine.travel("2020-11-22"):  # A Sunday the week before
        runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["lunch"])
        runner.invoke(main.app, ["stop", "16:30"])
    # The Monday is intentionally excluded
    with time_machine.travel("2020-11-24"):  # A Tuesday
        runner.invoke(main.app, ["start", "08:02"])
        runner.invoke(main.app, ["lunch"])
        runner.invoke(main.app, ["stop", "16:30"])

    with time_machine.travel("2020-11-25"):  # A Wednesday
        runner.invoke(main.app, ["start", "08:02"])
        runner.invoke(main.app, ["lunch", "25"])
        runner.invoke(main.app, ["stop", "14:21"])
        runner.invoke(main.app, ["start", "15:01"])
        runner.invoke(main.app, ["stop", "17:27"])

        result = runner.invoke(main.app, ["view", "week"])  # Act
    write_captured_output(result.stdout)

    expected = [
        "2020-11-23 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "",
        "2020-11-24 | worked time: 7h 58min | lunch: 30min | daily flex: -2min",
        "  08:02-16:30 => 8h 28min",
        "",
        "2020-11-25 | worked time: 8h 20min | lunch: 25min | daily flex: 20min",
        "  08:02-14:21 => 6h 19min",
        "  15:01-17:27 => 2h 26min",
        "---",
        "Weekly flex: 18min",
    ]
    assert_captured_out_starts_with(expected, result.stdout)


def test_view_prev_week() -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with time_machine.travel("2020-11-22"):  # A Sunday the week before
        runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["lunch"])
        runner.invoke(main.app, ["stop", "16:30"])
    # The Monday is intentionally excluded
    with time_machine.travel("2020-11-24"):  # A Tuesday
        runner.invoke(main.app, ["start", "08:02"])
        runner.invoke(main.app, ["lunch"])
        runner.invoke(main.app, ["stop", "16:30"])

    with time_machine.travel("2020-11-25"):  # A Wednesday
        runner.invoke(main.app, ["start", "08:02"])
        runner.invoke(main.app, ["lunch", "25"])
        runner.invoke(main.app, ["stop", "14:21"])
        runner.invoke(main.app, ["start", "15:01"])
        runner.invoke(main.app, ["stop", "17:27"])
    # some time passes so it is the next week
    with time_machine.travel("2020-11-30"):  # The Monday next week
        result = runner.invoke(main.app, ["view", "prev_week"])  # Act
    write_captured_output(result.stdout)

    expected = [
        "2020-11-23 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "",
        "2020-11-24 | worked time: 7h 58min | lunch: 30min | daily flex: -2min",
        "  08:02-16:30 => 8h 28min",
        "",
        "2020-11-25 | worked time: 8h 20min | lunch: 25min | daily flex: 20min",
        "  08:02-14:21 => 6h 19min",
        "  15:01-17:27 => 2h 26min",
        "",
        "2020-11-26 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "",
        "2020-11-27 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "",
        "2020-11-28 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "",
        "2020-11-29 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "---",
        "Weekly flex: 18min",
    ]
    assert_captured_out_starts_with(expected, result.stdout)


def test_view_is_case_insensitive() -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with time_machine.travel("2020-11-24"):  # A Tuesday
        runner.invoke(main.app, ["start", "08:02"])

        result = runner.invoke(main.app, ["view", "ToDaY"])  # Act

    expected = [
        "2020-11-24 | worked time: 0h 0min | lunch: 0min | daily flex: 0min",
        "  08:02-",
    ]
    assert_captured_out_starts_with(expected, result.stdout)


def test_summary_month() -> None:
    main.cfg.datafile = "2023-01-timesheet.json"
    with time_machine.travel("2023-01-03"):  # A Tuesday
        runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["lunch"])
        runner.invoke(main.app, ["stop", "16:30"])
    # Wednesday is intentionally excluded
    with time_machine.travel("2023-01-05"):  # A Thursday
        runner.invoke(main.app, ["start", "08:02"])
        runner.invoke(main.app, ["lunch"])
        runner.invoke(main.app, ["stop", "16:30"])
    with time_machine.travel("2023-01-07"):  # A Saturday
        runner.invoke(main.app, ["timeoff", "8"])
        runner.invoke(main.app, ["start", "10:00"])
        runner.invoke(main.app, ["stop", "11:30"])
    with time_machine.travel("2023-01-09"):  # Monday the next week
        runner.invoke(main.app, ["start", "08:02"])
        runner.invoke(main.app, ["lunch", "25"])
        runner.invoke(main.app, ["stop", "14:21"])
        runner.invoke(main.app, ["start", "15:01"])
        runner.invoke(main.app, ["stop", "17:27"])

        result = runner.invoke(main.app, ["summary"])  # Act
    write_captured_output(result.stdout)

    expected = [
        "┏━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┓",
        "┃ week ┃ date       ┃ worked time ┃ daily flex ┃ time off ┃",
        "┡━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━┩",
        "│      │ 2023-01-01 │             │            │          │",
        "├──────┼────────────┼─────────────┼────────────┼──────────┤",
        "│ 1    │ 2023-01-02 │             │            │          │",
        "│      │ 2023-01-03 │ 8h 0min     │ 0min       │          │",
        "│      │ 2023-01-04 │             │            │          │",
        "│      │ 2023-01-05 │ 7h 58min    │ -2min      │          │",
        "│      │ 2023-01-06 │             │            │          │",
        "│      │ 2023-01-07 │ 1h 30min    │ 1h 30min   │ 8h 0min  │",
        "│      │ 2023-01-08 │             │            │          │",
        "├──────┼────────────┼─────────────┼────────────┼──────────┤",
        "│ 2    │ 2023-01-09 │ 8h 20min    │ 20min      │          │",
        "└──────┴────────────┴─────────────┴────────────┴──────────┘",
        "",
        "week 1: 17h 28min",
        "week 2: 8h 20min",
        "Worked 25h 48min of 24 hour(s) => monthly flex: 1h 48min",
    ]
    assert_captured_out_starts_with(expected, result.stdout)


def test_timeoff_half_day() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with time_machine.travel("2021-04-02"):  # A Friday
        runner.invoke(main.app, ["timeoff", "4"])
        runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["stop", "12:02"])

        ts = load_timesheet()
        assert ts.today.time_off_minutes == 4 * 60
        assert ts.today.flex_minutes == 2


def test_timeoff_full_day() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with time_machine.travel("2021-04-02"):  # A Friday
        runner.invoke(main.app, ["timeoff", "8"])

        ts = load_timesheet()
        assert ts.today.time_off_minutes == 8 * 60
        assert ts.today.flex_minutes == 0


def test_timeoff_recalcs_flex() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with time_machine.travel("2021-04-02"):  # A Friday
        runner.invoke(main.app, ["start", "08:00"])
        runner.invoke(main.app, ["stop", "12:02"])

        ts = load_timesheet()
        assert ts.today.time_off_minutes == 0
        assert ts.today.flex_minutes == -4 * 60 + 2

        runner.invoke(main.app, ["timeoff", "4"])
        ts = load_timesheet()
        assert ts.today.time_off_minutes == 4 * 60
        assert ts.today.flex_minutes == 2


def test_timeoff_negative_input() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with time_machine.travel("2021-04-02"):  # A Friday
        result = runner.invoke(main.app, ["timeoff", "-1"], catch_exceptions=False)
        result.exception = ValueError("Invalid timeoff value, must be an int between 0 and 8 inclusive.")


def test_timeoff_more_than_a_workday() -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with time_machine.travel("2021-04-02"):  # A Friday
        result = runner.invoke(main.app, ["timeoff", "9"], catch_exceptions=False)
        result.exception = ValueError("Invalid timeoff value, must be an int between 0 and 8 inclusive.")


@pytest.mark.parametrize(
    "mins,expected",
    [(50, "50min"), (70, "1h 10min"), (-20, "-20min"), (-70, "-1h 10min")],
)
def test_fmt_mins(mins, expected) -> None:
    assert fmt_mins(mins) == expected


def test_worked_time() -> None:
    runner.invoke(main.app, ["start", "08:00"])
    runner.invoke(main.app, ["stop", "09:00"])  # Worked 1 hour

    runner.invoke(main.app, ["start", "12:00"])
    runner.invoke(main.app, ["stop", "14:00"])  # Worked 2 more hours

    ts = load_timesheet()
    assert ts.today.worked_time == 3 * 60


def test_worked_time_with_lunch() -> None:
    runner.invoke(main.app, ["start", "08:00"])
    runner.invoke(main.app, ["stop", "09:00"])
    runner.invoke(main.app, ["lunch", "25"])

    ts = load_timesheet()
    # Worked 35 min and had 25 min lunch
    assert ts.today.worked_time == 35
    assert ts.today.lunch == 25


def test_worked_time_with_no_block_stopped() -> None:
    runner.invoke(main.app, ["start", "08:10"])
    runner.invoke(main.app, ["lunch"])

    ts = load_timesheet()
    assert ts.today.worked_time == 0


def test_worked_time_with_a_block_not_stopped() -> None:
    runner.invoke(main.app, ["start", "08:10"])
    runner.invoke(main.app, ["stop", "08:30"])  # Worked 2 mins
    runner.invoke(main.app, ["start", "08:50"])

    ts = load_timesheet()
    assert ts.today.worked_time == 20


def test_comment_with_an_empty_comment() -> None:
    runner.invoke(main.app, ["start", "08:10"])
    handle_command("comment")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment is None


def test_comment_with_a_block_not_stopped() -> None:
    runner.invoke(main.app, ["start", "08:10"])
    handle_command("comment", "some comment added to this workblock")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment == "some comment added to this workblock"


def test_comment_overwrites_previous_comment() -> None:
    runner.invoke(main.app, ["start", "08:10"])
    handle_command("comment", "some comment added to this workblock")
    handle_command("comment", "new fancy comment")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment == "new fancy comment"


def test_comment_with_workblock() -> None:
    handle_command("comment", "some comment added to this workblock")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment is None


def test_comment_with_no_open_workblock() -> None:
    runner.invoke(main.app, ["start", "08:10"])
    runner.invoke(main.app, ["stop", "08:15"])
    handle_command("comment", "some comment added to this workblock")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment is None
