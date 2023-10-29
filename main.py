import contextlib

from src.worktimer import cfg, fmt_mins, handle_command, load_timesheet, print_menu


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
    run()
