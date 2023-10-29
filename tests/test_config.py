from collections import OrderedDict
from pathlib import Path

import main


def test_default_value_datafile_dir_is_in_home_folder(mocker) -> None:
    expected = Path(Path.home(), ".worktimer")
    mocker.patch("src.config.dotenv_values", return_value=OrderedDict())
    main.cfg.reload()

    assert main.cfg.datafile_dir == expected


def test_datafile_dir_is_working_dir_if_mode_is_dev(mocker) -> None:
    expected = Path(".worktimer")
    mocker.patch("src.config.dotenv_values", return_value=OrderedDict({"mode": "dev"}))
    main.cfg.reload()

    assert main.cfg.datafile_dir == expected


def test_run_prints_the_app_runs_in_dev_mode_if_dev_mode_set(capsys, mocker) -> None:
    mocker.patch("src.config.dotenv_values", return_value=OrderedDict({"mode": "dev"}))
    mocker.patch("builtins.input", return_value="q")
    main.cfg.reload()

    main.run()

    captured = capsys.readouterr()
    assert "Running in dev mode." in captured.out
