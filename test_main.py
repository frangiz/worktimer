from datetime import time
from pathlib import Path
from typing import Any, List
from unittest.mock import patch

import pytest  # type: ignore
from _pytest.capture import CaptureResult
from freezegun import freeze_time  # type: ignore

import main
from main import (
    Project,
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
    project_files = main.cfg.datafile_dir.glob("projects.json")
    for f in project_files:
        f.unlink()
    captured_output = Path(main.cfg.datafile_dir, "captured_output.txt")
    if captured_output.exists():
        captured_output.unlink()


def teardown_function(func: Any) -> None:
    main.cfg.workhours_one_day = 8


def write_captured_output(captured_output: str) -> None:
    Path(main.cfg.datafile_dir, "captured_output.txt").write_text(captured_output)


def assert_captured_out_starts_with(
    expected: List[str], captured: CaptureResult
) -> None:
    assert expected == captured.out.split("\n")[: len(expected)]


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
    assert_captured_out_starts_with(expected, captured)


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
    assert_captured_out_starts_with(expected, captured)


def test_view_today_with_a_comment(capsys) -> None:
    main.cfg.datafile = "2020-11-timesheet.json"
    with freeze_time("2020-11-24"):  # A Tuesday
        handle_command("start 08:00")
        handle_command("stop 09:00 Worked on solving the crazy hard bug.")
        handle_command("start 10:00")
        handle_command("comment working some more on the bug")
        capsys.readouterr()

        handle_command("view")  # Act
    captured = capsys.readouterr()

    expected = [
        "2020-11-24 | worked time: 1h 0min | lunch: 0min | daily flex: -7h 0min",
        "  08:00-09:00 => 1h 0min",
        "    Worked on solving the crazy hard bug.",
        "  10:00-",
        "    working some more on the bug",
    ]
    assert_captured_out_starts_with(expected, captured)


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
    write_captured_output(captured.out)

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
    assert_captured_out_starts_with(expected, captured)


def test_view_prev_week(capsys) -> None:
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
    # some time passes so it is the next week
    with freeze_time("2020-11-30"):  # The Monday next week
        capsys.readouterr()
        handle_command("view prev_week")  # Act
    captured = capsys.readouterr()
    write_captured_output(captured.out)

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
    assert_captured_out_starts_with(expected, captured)


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
    assert_captured_out_starts_with(expected, captured)


def test_summary_month(capsys) -> None:
    main.cfg.datafile = "2023-01-timesheet.json"
    with freeze_time("2023-01-03"):  # A Tuesday
        handle_command("start 08:00")
        handle_command("lunch")
        handle_command("stop 16:30")
    # Wednesday is intentionally excluded
    with freeze_time("2023-01-05"):  # A Thursday
        handle_command("start 08:02")
        handle_command("lunch")
        handle_command("stop 16:30")
    with freeze_time("2023-01-07"):  # A Saturday
        handle_command("timeoff 8")
        handle_command("start 10:00")
        handle_command("stop 11:30")
    with freeze_time("2023-01-09"):  # Monday the next week
        handle_command("start 08:02")
        handle_command("lunch 25")
        handle_command("stop 14:21")
        handle_command("start 15:01")
        handle_command("stop 17:27")
        capsys.readouterr()

        handle_command("summary")  # Act
    captured = capsys.readouterr()
    write_captured_output(captured.out)

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
    assert_captured_out_starts_with(expected, captured)


def test_timeoff_half_day(capsys) -> None:
    main.cfg.datafile = "2021-04-timesheet.json"
    with freeze_time("2021-04-02"):  # A Friday
        handle_command("timeoff 4")
        handle_command("start 08:00")
        captured = capsys.readouterr()
        write_captured_output(captured.out)
        handle_command("stop 12:02")

        ts = load_timesheet()
        assert ts.today.time_off_minutes == 4 * 60
        assert ts.today.flex_minutes == 2
        assert (
            "Estimated end time for today with 30 min lunch is 12:30:00" in captured.out
        )


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


def test_comment_with_an_empty_comment() -> None:
    handle_command("start 08:10")
    handle_command("comment ")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment is None


def test_comment_with_a_block_not_stopped() -> None:
    handle_command("start 08:10")
    handle_command("comment some comment added to this workblock")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment == "some comment added to this workblock"


def test_comment_overwrites_previous_comment() -> None:
    handle_command("start 08:10")
    handle_command("comment some comment added to this workblock")
    handle_command("comment new fancy comment")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment == "new fancy comment"


def test_comment_with_workblock() -> None:
    handle_command("comment some comment added to this workblock")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment is None


def test_comment_with_no_open_workblock() -> None:
    handle_command("start 08:10")
    handle_command("stop 08:15")
    handle_command("comment some comment added to this workblock")

    ts = load_timesheet()
    assert ts.today.last_work_block.comment is None


def test_handle_empty_command(capsys) -> None:
    handle_command("")
    assert "No command given" in capsys.readouterr().out


def test_create_project() -> None:
    handle_command("create_project test_project")
    projects = main.load_projects()
    assert len(projects) == 1
    expected = Project(id=1, name="test_project", deleted=False)
    assert projects[0] == expected
    assert projects.get_project_by_id(1) == expected


def test_create_project_with_empty_name() -> None:
    with pytest.raises(ValueError, match="Project name cannot be empty"):
        handle_command("create_project")


def test_create_project_with_long_name() -> None:
    with pytest.raises(
        ValueError, match="Project name cannot be longer than 50 characters"
    ):
        handle_command("create_project " + "a" * 51)


def test_create_project_with_existing_name() -> None:
    handle_command("create_project test_project")
    with pytest.raises(
        ValueError, match="Project with name 'test_project' already exists"
    ):
        handle_command("create_project test_project")


def test_list_projects(capsys) -> None:
    handle_command("create_project test_project")
    handle_command("list_projects")
    captured = capsys.readouterr()
    assert "1: test_project" in captured.out


def test_delete_project() -> None:
    handle_command("create_project test_project")
    handle_command("delete_project 1")
    projects = main.load_projects()
    assert len(projects) == 1
    assert projects.get_project_by_id(1).deleted


def test_delete_project_with_non_existing_id() -> None:
    with pytest.raises(ValueError, match="No project with id 2"):
        handle_command("delete_project 2")


def test_start_workblock_gets_no_project_when_no_projects_is_added() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23"):  # A Wednesday
        handle_command("start 08:00")
        ts = load_timesheet()
        assert ts.today.last_work_block.project_id is None


def test_start_workblock_with_selecting_project() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project project2")
    with freeze_time("2020-09-23"):  # A Wednesday
        with patch("builtins.input", side_effect=["1"]):
            handle_command("start 08:00")
        ts = load_timesheet()
        assert ts.today.last_work_block.project_id == 1


def test_prompt_for_project_handles_invalid_input() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project project2")
    with freeze_time("2020-09-23"):  # A Wednesday
        with patch("builtins.input", side_effect=["abc", "1"]):
            handle_command("start 08:00")
        ts = load_timesheet()
        assert ts.today.last_work_block.project_id == 1


def test_start_second_workblock_with_selecting_project() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23"):  # A Wednesday
        handle_command("start 08:00")
        handle_command("stop 08:01")
        handle_command("create_project second project")
        with patch("builtins.input", side_effect=["1"]):
            handle_command("start 08:02")
        ts = load_timesheet()
        assert ts.today.work_blocks[0].project_id is None
        assert ts.today.last_work_block.project_id == 1


def test_possible_to_start_workblock_without_selecting_an_existing_project() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project some_project")
    with freeze_time("2020-09-23"):  # A Wednesday
        with patch("builtins.input", side_effect=["0"]):
            handle_command("start 08:00")
        ts = load_timesheet()
        assert ts.today.last_work_block.project_id is None


def test_rename_project(capsys) -> None:
    handle_command("create_project proj1")
    handle_command("rename_project 1 new proj name")
    handle_command("list_projects")
    captured = capsys.readouterr()
    assert "1: new proj name" in captured.out


def test_rename_project_with_no_provided_id() -> None:
    with pytest.raises(ValueError, match="No project id provided"):
        handle_command("rename_project")


def test_rename_project_with_no_provided_name() -> None:
    with pytest.raises(ValueError, match="Project name cannot be empty"):
        handle_command("rename_project 1")


def test_rename_project_with_empty_name() -> None:
    handle_command("create_project proj1")
    with pytest.raises(ValueError, match="Project name cannot be empty"):
        handle_command("rename_project 1")


def test_rename_project_with_long_name() -> None:
    handle_command("create_project proj1")
    with pytest.raises(
        ValueError, match="Project name cannot be longer than 50 characters"
    ):
        handle_command("rename_project 1 " + "a" * 51)


def test_rename_project_with_existing_name() -> None:
    handle_command("create_project test_project")
    handle_command("create_project other_project")
    with pytest.raises(
        ValueError, match="Project with name 'other_project' already exists"
    ):
        handle_command("rename_project 1 other_project")


def test_recalc_only_affects_files_from_current_year() -> None:
    # Set up previous year data
    with freeze_time("2020-09-23"):  # A Wednesday
        main.cfg.datafile = "2020-09-timesheet.json"
        handle_command("start 08:00")
        handle_command("lunch 45")
        handle_command("stop 16:30")
        prev_ts = load_timesheet()
        prev_flex = prev_ts.today.flex_minutes
        prev_ts.today.flex_minutes = 10  # -15 is correct calculated value

    # Set up current year data
    with freeze_time("2024-11-22"):  # A Friday
        main.cfg.datafile = "2024-11-timesheet.json"
        handle_command("start 08:00")
        handle_command("lunch 30")
        handle_command("stop 16:30")

        # Trigger recalc
        main.cfg.workhours_one_day = 7
        handle_command("recalc")

        # Verify current year was affected
        curr_ts = load_timesheet()
        assert curr_ts.today.flex_minutes != 0

        # Verify previous year was not affected
        main.cfg.datafile = "2020-09-timesheet.json"
        unchanged_ts = load_timesheet()
        assert unchanged_ts.get_day("2020-09-23").flex_minutes == prev_flex


def test_stop_workblock_get_no_project_when_no_projects_is_added() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23"):  # A Wednesday
        handle_command("start 08:00")
        handle_command("stop 16:30")

        ts = load_timesheet()
        assert ts.today.last_work_block.project_id is None


def test_stop_prompts_for_project_when_projects_exists() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project test_project")

    with freeze_time("2020-09-23"):  # A Wednesday
        with patch("builtins.input", side_effect=["0"]):
            handle_command("start 08:00")
        with patch("builtins.input", side_effect=["1"]):
            handle_command("stop 16:30")

        ts = load_timesheet()
        assert ts.today.last_work_block.project_id == 1


def test_stop_workblock_with_no_project_selected() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project test_project")

    with freeze_time("2020-09-23"):
        with patch("builtins.input", side_effect=["1"]):
            handle_command("start 08:00")
        with patch("builtins.input", side_effect=["0"]):
            handle_command("stop 16:30")

        ts = load_timesheet()
        assert ts.today.last_work_block.project_id is None


def test_stop_workblock_suggests_existing_project() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project test_project")

    with freeze_time("2020-09-23"):
        # Start with project 1 selected
        with patch("builtins.input", side_effect=["1"]):
            handle_command("start 08:00")

        # Simulate pressing enter (empty input) to accept suggested project
        with patch("builtins.input", side_effect=[""]):
            handle_command("stop 16:30")

        ts = load_timesheet()
        assert ts.today.last_work_block.project_id == 1


def test_switch_command_uses_current_time_when_no_time_given() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23 09:00"):
        handle_command("start 08:00")
        handle_command("switch")

        ts = load_timesheet()
        assert ts.today.work_blocks[0].stop == time.fromisoformat("09:00:00")
        assert ts.today.work_blocks[1].start == time.fromisoformat("09:00:00")


def test_switch_command_with_specific_time() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23"):
        handle_command("start 08:00")
        handle_command("switch 10:30")

        ts = load_timesheet()
        assert ts.today.work_blocks[0].stop == time.fromisoformat("10:30:00")
        assert ts.today.work_blocks[1].start == time.fromisoformat("10:30:00")


def test_switch_command_with_project_selection() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project test_project")

    with freeze_time("2020-09-23"):
        with patch("builtins.input", side_effect=["1"]):
            handle_command("start 08:00")
        with patch("builtins.input", side_effect=["1", "1"]):
            handle_command("switch 10:30")

        ts = load_timesheet()
        assert ts.today.work_blocks[0].project_id == 1
        assert ts.today.work_blocks[1].project_id == 1


def test_switch_command_with_no_active_workblock() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23"):
        with pytest.raises(ValueError, match="No active work block to switch from"):
            handle_command("switch 10:30")


def test_switch_command_with_time_before_workblock_start() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23"):
        handle_command("start 08:00")

        with pytest.raises(
            ValueError,
            match="Switch time 07:00 cannot be before workblock start time 08:00",
        ):
            handle_command("switch 07:00")

        # Verify workblock was not modified
        ts = load_timesheet()
        assert ts.today.work_blocks[0].start == time.fromisoformat("08:00:00")
        assert ts.today.work_blocks[0].stop is None
        assert len(ts.today.work_blocks) == 1


def test_switch_command_multiple_times() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23"):
        handle_command("start 08:00")
        handle_command("switch 09:00")
        handle_command("switch 10:00")

        ts = load_timesheet()
        assert len(ts.today.work_blocks) == 3
        assert ts.today.work_blocks[0].stop == time.fromisoformat("09:00:00")
        assert ts.today.work_blocks[1].start == time.fromisoformat("09:00:00")
        assert ts.today.work_blocks[1].stop == time.fromisoformat("10:00:00")
        assert ts.today.work_blocks[2].start == time.fromisoformat("10:00:00")


def test_switch_command_with_invalid_time_format() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    with freeze_time("2020-09-23"):
        handle_command("start 08:00")
        with pytest.raises(ValueError, match="Invalid time format"):
            handle_command("switch 1030")


def test_switch_command_between_projects() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project project1")
    handle_command("create_project project2")

    with freeze_time("2020-09-23"):
        with patch("builtins.input", side_effect=["1"]):
            handle_command("start 08:00")
        with patch("builtins.input", side_effect=["1", "2"]):
            handle_command("switch 09:00")

        ts = load_timesheet()
        assert ts.today.work_blocks[0].project_id == 1
        assert ts.today.work_blocks[1].project_id == 2


def test_switch_command_changes_project_on_current_and_new_workblock() -> None:
    main.cfg.datafile = "2020-09-timesheet.json"
    handle_command("create_project project1")
    handle_command("create_project project2")
    handle_command("create_project project3")

    with freeze_time("2020-09-23"):
        # Start with project1
        with patch("builtins.input", side_effect=["1"]):
            handle_command("start 08:00")

        # Switch: change current to project2, new block to project3
        with patch("builtins.input", side_effect=["2", "3"]):
            handle_command("switch 09:00")

        ts = load_timesheet()
        assert ts.today.work_blocks[0].project_id == 2  # Changed to project2
        assert ts.today.work_blocks[1].project_id == 3  # New block with project3
