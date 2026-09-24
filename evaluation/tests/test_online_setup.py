"""Setup regression tests: use temporary checkouts and never sudo or load modules."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\nset -eu\n" + body)
    path.chmod(0o755)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout with spaces"
    (root / "setup").mkdir(parents=True)
    for name in ("setup.sh", "sources.sh", "versions.env"):
        shutil.copy2(ROOT / "setup" / name, root / "setup" / name)
    batman = root / "kernel/batman-adv-emulated-wifi"
    batman.mkdir(parents=True)
    shutil.copy2(ROOT / "kernel/batman-adv-emulated-wifi/profile.sh", batman / "profile.sh")
    log = root / "calls"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BATADV_", "MACE_"))}
    env["CALL_LOG"] = str(log)
    tools = root / "tools"
    tools.mkdir()
    env["PATH"] = str(tools) + ":" + os.environ["PATH"]
    executable(tools / "uname", 'if [[ "$1" == -r ]]; then echo 6.8.0-test; else echo Linux; fi\n')
    # Guard against unintended calls. No real package or kernel operation can run.
    for name in ("sudo", "apt-get", "modprobe", "insmod", "rmmod", "ip", "mokutil", "reboot", "git"):
        executable(tools / name, f'echo unexpected:{name} >> "$CALL_LOG"; exit 90\n')
    executable(root / "setup/python-core.sh", 'echo core >> "$CALL_LOG"\n')
    executable(root / "setup/doctor.sh", 'echo doctor:"$*" >> "$CALL_LOG"; exit "${DOCTOR_EXIT:-0}"\n')
    executable(batman / "module-control.sh", '''
echo controller:"$*" >> "$CALL_LOG"
[[ "$1" == verify ]] || exit 91
''')
    executable(batman / "build.sh", 'echo unexpected:build >> "$CALL_LOG"; exit 92\n')
    return root, env, log


def run_setup(checkout, *args):
    root, env, _ = checkout
    return subprocess.run([str(root / "setup/setup.sh"), *args], env=env,
                          capture_output=True, text=True)


def test_check_is_read_only_and_batman_is_optional(checkout):
    root, _, log = checkout
    result = run_setup(checkout, "--check", "--skip-system")
    assert result.returncode == 0, result.stderr
    assert "BATMAN: none" in result.stdout
    assert not log.exists()
    assert not (root / ".build").exists()


def test_package_plan_separates_optional_kernel_dependencies(checkout):
    # Automatic package recipes are deliberately limited to Ubuntu 24.04.
    os_release = Path("/etc/os-release").read_text()
    if 'ID=ubuntu\n' not in os_release or 'VERSION_ID="24.04"' not in os_release:
        pytest.skip("Ubuntu package plan")
    basic = run_setup(checkout, "--check")
    assert basic.returncode == 0, basic.stderr
    assert "linux-headers" not in basic.stdout
    assert " batctl" not in basic.stdout
    mesh = run_setup(checkout, "--check", "--batman", "emulated_wifi")
    assert mesh.returncode == 0, mesh.stderr
    assert "linux-headers-6.8.0-test" in mesh.stdout
    assert " batctl" in mesh.stdout


@pytest.mark.parametrize("args", [("--batman",), ("--batman", "typo"), ("--skip-system", "--system-only"), ("--unknown",)])
def test_invalid_options_fail_before_changes(checkout, args):
    root, _, log = checkout
    result = run_setup(checkout, *args)
    assert result.returncode != 0
    assert not log.exists()
    assert not (root / ".build").exists()


def test_unsupported_kernel_only_blocks_batman(checkout):
    root, _, log = checkout
    executable(root / "tools/uname", 'if [[ "$1" == -r ]]; then echo 9.9.0-test; else echo Linux; fi\n')
    assert run_setup(checkout, "--check", "--skip-system").returncode == 0
    mesh = run_setup(checkout, "--check", "--skip-system", "--batman", "emulated_wifi")
    assert mesh.returncode != 0
    assert "no validated build profile" in mesh.stderr
    assert not log.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="Setup intentionally rejects root")
def test_resume_reuses_artifact_and_reports_manual_pending(checkout):
    root, env, log = checkout
    artifact = root / "kernel/batman-adv-emulated-wifi/build/6.8.0-test/batman-adv.ko"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"signed module fixture")
    env["DOCTOR_EXIT"] = "1"
    for _ in range(2):
        result = run_setup(checkout, "--skip-system", "--batman", "emulated_wifi")
        assert result.returncode == 2, result.stderr
        assert "existing signature preserved" in result.stdout
        assert "[PENDING]" in result.stderr
        assert artifact.read_bytes() == b"signed module fixture"
    calls = log.read_text().splitlines()
    assert calls.count("controller:verify emulated_wifi") == 2
    assert not any("unexpected:" in line for line in calls)
    assert not any("verify native" in line for line in calls)


@pytest.mark.skipif(os.geteuid() == 0, reason="Setup intentionally rejects root")
def test_base_setup_does_not_touch_batman(checkout):
    _, _, log = checkout
    result = run_setup(checkout, "--skip-system")
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "core\n" in calls
    assert "--mode ip" in calls
    assert "controller:" not in calls
    assert "unexpected:" not in calls


def test_source_pin_and_dirty_cache_are_enforced(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(upstream), *args], text=True).strip()
    git("init", "-q")
    (upstream / "README").write_text("pinned content")
    git("add", "README")
    git("-c", "user.name=Setup test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    git("tag", "v1")
    commit = git("rev-parse", "HEAD")
    destination = tmp_path / "source cache"
    def fetch(expected):
        return subprocess.run(["bash", "-c", 'source "$1"; fetch_source "$2" "$3" v1 "$4"',
                               "bash", str(ROOT / "setup/sources.sh"), str(destination),
                               upstream.as_uri(), expected], capture_output=True, text=True)
    mismatch = fetch("0" * 40)
    assert mismatch.returncode != 0
    assert not destination.exists()
    assert not list(tmp_path.glob(".download.*"))
    assert fetch(commit).returncode == 0
    # A valid cache is reusable even when the remote is unavailable.
    upstream.rename(tmp_path / "unavailable")
    assert fetch(commit).returncode == 0
    (destination / "README").write_text("researcher's changes")
    modified = fetch(commit)
    assert modified.returncode != 0
    assert "Modified source cache" in modified.stderr
    assert (destination / "README").read_text() == "researcher's changes"


def test_doctor_runs_before_venv_exists(tmp_path):
    for file in ("setup/doctor.sh", "setup/doctor.py", "scripts/runtime.sh"):
        target = tmp_path / file
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(ROOT / file, target)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MACE_")}
    env["MACE_SETUP_PYTHON"] = sys.executable
    result = subprocess.run([str(tmp_path / "setup/doctor.sh"), "--json"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    diagnostic = json.loads(result.stdout)
    assert diagnostic["mode"] == "ip"
    assert not diagnostic["ok"]
    assert not (tmp_path / ".venv").exists()


def test_native_selection_is_literal_and_explicit_override_wins(tmp_path):
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    state = tmp_path / ".build/setup"
    state.mkdir(parents=True)
    selected = '/tmp/a path/$(touch NEVER)/batman-adv.ko'
    (state / ("native-module-" + os.uname().release)).write_text(selected + "\n")
    env = dict(os.environ, MACE_PYTHON=sys.executable)
    env.pop("BATADV_NATIVE_MODULE", None)
    def read_selection():
        return subprocess.check_output(["bash", "-c", 'source "$1"; printf "%s" "$BATADV_NATIVE_MODULE"',
                                        "bash", str(tmp_path / "scripts/runtime.sh")], cwd=tmp_path, env=env, text=True)
    assert read_selection() == selected
    assert not (tmp_path / "NEVER").exists()
    env["BATADV_NATIVE_MODULE"] = "/explicit/override.ko"
    assert read_selection() == "/explicit/override.ko"


@pytest.mark.skipif(os.geteuid() == 0, reason="Setup intentionally rejects root")
def test_first_module_setup_builds_then_resumes_without_another_build(checkout):
    root, _, log = checkout
    # External downloads/builds are exercised separately; here verify orchestration.
    (root / "setup/sources.sh").write_text(
        'fetch_source() { echo "fetch:$3:$4" >> "$CALL_LOG"; mkdir -p "$1"; }\n')
    batman = root / "kernel/batman-adv-emulated-wifi"
    executable(batman / "module-control.sh", '''
echo controller:"$*" >> "$CALL_LOG"
[[ "$1" == verify && "$2" == emulated_wifi && -f "$BATADV_MODULE_ARTIFACT" ]]
''')
    executable(batman / "build.sh", '''
echo build:"$1" >> "$CALL_LOG"
[[ -d "$BATADV_SOURCE_DIR" && "$1" == emulated_wifi ]]
mkdir -p "$OUTPUT_DIR"
printf fixture > "$OUTPUT_DIR/batman-adv.ko"
''')
    for _ in range(2):
        result = run_setup(checkout, "--skip-system", "--batman", "emulated_wifi")
        assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert calls.count("build:emulated_wifi") == 1
    assert calls.count("fetch:v2024.0:7ee009fb21955bc7977d96b00eb8362a558d0d3a") == 1
    assert not any("unexpected:" in line for line in calls)


@pytest.mark.skipif(os.geteuid() == 0, reason="Setup intentionally rejects root")
def test_system_phase_only_installs_missing_packages(checkout):
    os_release = Path("/etc/os-release").read_text()
    if 'ID=ubuntu\n' not in os_release or 'VERSION_ID="24.04"' not in os_release:
        pytest.skip("Ubuntu package recipe")
    root, _, log = checkout
    executable(root / "tools/dpkg-query", '''
if [[ "${@: -1}" == tshark ]]; then exit 1; fi
printf 'install ok installed'
''')
    executable(root / "tools/sudo", 'echo simulated-sudo:"$*" >> "$CALL_LOG"\n')
    result = run_setup(checkout, "--system-only", "--yes")
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "simulated-sudo:apt-get update", "simulated-sudo:apt-get install -y tshark"]
