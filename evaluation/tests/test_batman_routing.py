"""Routing preflight regressions: no kernel writes or CORE networks."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from classes.runner.emulator import Emulator


CONFIG = {"networks": [{"routing": "batman", "hardif_behavior": "emulated_wifi"}]}
PARAMETER = Path("/sys/module/batman_adv/parameters/routing_algo")


def test_preflight_accepts_selected_v_without_parsing_batctl_report():
    emulator = Emulator.__new__(Emulator)
    with patch("classes.runner.emulator.subprocess.run") as mode_check, \
         patch("classes.runner.emulator.subprocess.check_output", side_effect=AssertionError("batctl is unnecessary")), \
         patch.object(Path, "read_text", autospec=True, return_value="BATMAN_V\n") as read, \
         patch("classes.runner.emulator.Scenario") as scenario:
        emulator.setup(CONFIG)
        read.assert_called_once_with(PARAMETER, encoding="utf-8")
        mode_check.assert_called_once()
        scenario.assert_called_once_with(CONFIG)


@pytest.mark.parametrize("selected", ["BATMAN_IV\n", "", "Available routing algorithms:\nBATMAN_IV\nBATMAN_V\n"])
def test_preflight_rejects_wrong_or_unrecognized_selection(selected):
    emulator = Emulator.__new__(Emulator)
    with patch("classes.runner.emulator.subprocess.run"), \
         patch.object(Path, "read_text", return_value=selected), \
         patch("classes.runner.emulator.Scenario") as scenario:
        with pytest.raises(RuntimeError, match="selected:"):
            emulator.setup(CONFIG)
        scenario.assert_not_called()


def test_preflight_reports_unreadable_parameter_before_creating_scenario():
    emulator = Emulator.__new__(Emulator)
    with patch("classes.runner.emulator.subprocess.run"), \
         patch.object(Path, "read_text", side_effect=PermissionError("fixture")), \
         patch("classes.runner.emulator.Scenario") as scenario:
        with pytest.raises(RuntimeError, match="Cannot read selected BATMAN routing algorithm"):
            emulator.setup(CONFIG)
        scenario.assert_not_called()


def test_metadata_reads_algorithm_without_privileged_batctl_query(monkeypatch, capsys):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "mace_module_status_test", root / "kernel/batman-adv-emulated-wifi/module_status.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reader = Mock(side_effect=lambda path: "BATMAN_V" if path == PARAMETER else None)
    run = Mock(return_value=None)
    monkeypatch.setattr(module, "read", reader)
    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(module, "sha256", lambda path: None)
    monkeypatch.setattr(module, "git_metadata", lambda: {})
    monkeypatch.setattr(module, "module_refcount", lambda: 0)
    monkeypatch.setattr(Path, "is_dir", lambda path: path == Path("/sys/module/batman_adv"))
    monkeypatch.setattr("sys.argv", ["module_status.py"])
    module.main()
    data = json.loads(capsys.readouterr().out)
    assert data["routing_algorithm"] == "BATMAN_V"
    reader.assert_any_call(PARAMETER)
    assert not any(call.args == ("batctl", "routing_algo") for call in run.call_args_list)
