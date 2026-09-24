import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from classes.core_compat import add_node
from evaluation.inject_scenario import inject


class PortabilityTests(unittest.TestCase):
    def test_import_does_not_require_paparazzi(self):
        code = '''
import importlib.abc, sys
class BlockPaparazzi(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith("pprzlink"):
            raise AssertionError("PAPARAZZI imported on headless evaluation path")
sys.meta_path.insert(0, BlockPaparazzi())
import pymace
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shell_paths_are_literal_and_json_paths_remain_unquoted(self):
        path = '/tmp/research project/it\'s $HOME; $(false) & data'
        config = {"settings": {"experiment_clock_file": "__EXPERIMENT_CLOCK__"},
                  "nodes": [{"function": [shlex.join(["/bin/bash", "-c", "printf '%s' __CRDT_BIN__"])]}]}
        out = inject(config, {"__CRDT_BIN__": path, "__EXPERIMENT_CLOCK__": path})
        result = subprocess.run(shlex.split(out["nodes"][0]["function"][0]), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, path)
        self.assertEqual(out["settings"]["experiment_clock_file"], path)

    def test_modern_core_receives_name_and_position_separately(self):
        class ModernSession:
            def add_node(self, cls, *, name, position):
                return name, position.x, position.y

        self.assertEqual(add_node(ModernSession(), object, name="node7", x=17, y=25),
                         ("node7", 17, 25))

    def test_legacy_core_receives_options(self):
        class LegacySession:
            def add_node(self, cls, *, options):
                return options.name, options.x, options.y

        self.assertEqual(add_node(LegacySession(), object, name="node7", x=17, y=25),
                         ("node7", 17, 25))

    def test_core_setup_supports_removed_write_nodes_and_reports_service_errors(self):
        from classes.runner.emulator import Emulator

        emulator = Emulator.__new__(Emulator)
        session = Mock(spec=["set_state", "instantiate"])
        session.instantiate.return_value = []
        emulator.coreemu = Mock()
        emulator.coreemu.create_session.return_value = session
        emulator.scenario = Mock()
        emulator.setup_core()
        emulator.scenario.setup_links.assert_called_once_with(session)
        session.instantiate.return_value = [RuntimeError("service failed")]
        with self.assertRaisesRegex(RuntimeError, "service failed"):
            emulator.setup_core()

    def test_direct_emulated_wifi_run_refuses_wrong_mode_before_creating_scenario(self):
        from classes.runner.emulator import Emulator

        emulator = Emulator.__new__(Emulator)
        config = {"networks": [{"routing": "batman", "hardif_behavior": "emulated_wifi"}]}
        with patch("classes.runner.emulator.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "module_status")) as status, \
             patch("classes.runner.emulator.Scenario") as scenario:
            with self.assertRaises(subprocess.CalledProcessError):
                emulator.setup(config)
            self.assertIn("emulated_wifi", status.call_args.args[0])
            scenario.assert_not_called()

    def test_generated_scenario_uses_current_environment(self):
        with tempfile.TemporaryDirectory(prefix="mace paths ") as tmp:
            folder = Path(tmp)
            scenario = json.loads((ROOT / "scenarios/batman_wifi_smoke/scenario.json").read_text())
            (folder / "scenario.json").write_text(json.dumps(scenario))
            env = dict(os.environ, SUDO_USER="researcher")
            result = subprocess.run([sys.executable, str(ROOT / "evaluation/generate_scenario.py"),
                                     str(folder), "broadcast"], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads((folder / "mace.json").read_text())
            self.assertEqual(config["settings"]["username"], "researcher")
            self.assertEqual(config["settings"]["report_folder"], str(ROOT / "reports") + "/")
            self.assertEqual(config["networks"][0]["hardif_behavior"], "emulated_wifi")
            script = shlex.split(config["nodes"][0]["function"][0])[2]
            self.assertIn(shlex.quote(sys.executable), script)
            # Capture must cover the startup barrier plus the application.
            self.assertIn("timeout -s INT 51 tcpdump", script)
            subprocess.run(["bash", "-n", "-c", script], check=True)

    def test_controller_accepts_compressed_native_and_rejects_tampered_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            native = folder / "batman-adv.ko.zst"
            native.write_bytes(b"native fixture")
            artifact = folder / "batman-adv.ko"
            artifact.write_bytes(b"experimental fixture")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            (folder / "batman-adv.ko.sha256").write_text(f"{digest}  batman-adv.ko\n")
            patch = ROOT / "kernel/batman-adv-emulated-wifi/patches/0002-batman-adv-2024.0-emulated-wifi.patch"
            (folder / "build-info.txt").write_text(
                f"artifact_sha256={digest}\npatch_sha256={hashlib.sha256(patch.read_bytes()).hexdigest()}\n"
                "kernel_release=6.8.0-test\nmodule_version=2024.0-macewifi1\n"
                "config_batman_adv_batman_v=y\n"
                "upstream_commit=7ee009fb21955bc7977d96b00eb8362a558d0d3a\n")
            uname = folder / "uname"
            uname.write_text("#!/bin/sh\necho 6.8.0-test\n")
            uname.chmod(0o755)
            modinfo = folder / "modinfo"
            modinfo.write_text(f'''#!{sys.executable}
import sys
a = sys.argv[1:]
if a[0] == "-n": print({str(native)!r})
elif a[0] == "-p":
    if not a[-1].endswith(".zst"): print("emulated_wifi:bool")
else:
    print({{"name":"batman_adv", "version":"2024.0" if a[-1].endswith(".zst") else "2024.0-macewifi1",
           "srcversion":"fixture", "vermagic":"6.8.0-test SMP"}}[a[1]])
''')
            modinfo.chmod(0o755)
            env = dict(os.environ, PATH=f"{folder}:{os.environ['PATH']}",
                       BATADV_MODULE_ARTIFACT=str(artifact))
            env.pop("BATADV_NATIVE_MODULE", None)
            controller = ROOT / "kernel/batman-adv-emulated-wifi/module-control.sh"
            result = subprocess.run([str(controller), "verify"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            # Wi-Fi emulation must also work on hosts without a native module.
            native.unlink()
            result = subprocess.run([str(controller), "verify", "emulated_wifi"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(controller), "verify", "native"], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            native.write_bytes(b"native fixture")
            artifact.write_bytes(b"tampered")
            result = subprocess.run([str(controller), "verify"], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum", result.stderr)


if __name__ == "__main__":
    unittest.main()
