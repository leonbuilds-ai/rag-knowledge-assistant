"""Lifecycle checks for the one-terminal launcher, without local services."""
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/dev.py"
spec = importlib.util.spec_from_file_location("rag_dev_launcher", SCRIPT)
dev = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dev
spec.loader.exec_module(dev)


class FakeProcess:
    def __init__(self, pid=123456789, code=None):
        self.pid, self.returncode = pid, code
        self.stdout = io.StringIO("fixture log\n")
        self.killed = False

    def poll(self):
        return self.returncode

    def send_signal(self, _):
        self.returncode = 0

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fixture", timeout)
        return self.returncode

    def kill(self):
        self.killed, self.returncode = True, -9


class LauncherLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.output = patch.object(dev, "say")
        self.output.start()

    def tearDown(self):
        self.output.stop()
        self.directory.cleanup()

    def service(self, ready=lambda: True, port=None):
        return dev.Service("Fixture", [sys.executable, "-c", "pass"], self.root, ready, port)

    def test_second_launcher_is_rejected_and_lock_can_be_reacquired(self):
        path = self.root / "launcher.lock"
        with dev.InstanceLock(path):
            with self.assertRaisesRegex(RuntimeError, "一键启动"):
                with dev.InstanceLock(path):
                    self.fail("duplicate launcher acquired the lock")
        with dev.InstanceLock(path):
            pass

    def test_reused_service_is_never_spawned_or_stopped(self):
        service = self.service()
        supervisor = dev.Supervisor([service], self.root)
        with patch.object(dev.subprocess, "Popen") as spawn:
            supervisor.inspect(service)
            supervisor.start(service)
            supervisor.shutdown()
        self.assertTrue(service.reused)
        spawn.assert_not_called()
        self.assertIsNone(service.process)

    def test_unknown_port_owner_aborts_before_starting_any_service(self):
        first = self.service(ready=lambda: False)
        busy = self.service(ready=lambda: False, port=8010)
        supervisor = dev.Supervisor([first, busy], self.root)
        with patch.object(dev, "port_in_use", return_value=True), patch.object(dev.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "8010"):
                supervisor.run()
        spawn.assert_not_called()

    def test_child_startup_failure_cleans_up_owned_process(self):
        process = FakeProcess(code=7)
        service = self.service(ready=lambda: False)
        supervisor = dev.Supervisor([service], self.root)
        with patch.object(dev.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "退出码 7"):
                supervisor.run()
        self.assertTrue(process.stdout.closed)
        self.assertTrue((self.root / "fixture.log").is_file())

    def test_ctrl_c_stops_owned_service_and_retains_reused_service(self):
        process = FakeProcess()
        owned = self.service()
        owned.process = process
        reused = self.service()
        reused.reused = True
        supervisor = dev.Supervisor([owned, reused], self.root)
        with patch.object(supervisor, "inspect"), patch.object(supervisor, "start"), \
                patch.object(dev.time, "sleep", side_effect=KeyboardInterrupt), \
                patch.object(dev.os, "killpg", side_effect=lambda *_: process.send_signal(0), create=True):
            with self.assertRaises(KeyboardInterrupt):
                supervisor.run()
        self.assertEqual(process.returncode, 0)
        self.assertFalse(process.killed)
        self.assertIsNone(reused.process)

    def test_unresponsive_owned_process_is_killed_after_grace_period(self):
        process = FakeProcess()
        process.send_signal = lambda _: None
        service = self.service()
        service.process = process
        with patch.object(dev.os, "killpg", return_value=None, create=True):
            dev.Supervisor([service], self.root).shutdown()
        self.assertTrue(process.killed)

    def test_real_failed_child_is_reaped(self):
        service = dev.Service("Failure", [sys.executable, "-c", "raise SystemExit(7)"], self.root, lambda: False)
        with self.assertRaisesRegex(RuntimeError, "退出码 7"):
            dev.Supervisor([service], self.root, timeout=10).run()
        self.assertEqual(service.process.poll(), 7)

    def test_stop_identifies_only_this_checkout_services(self):
        backend = self.root / "backend"
        frontend = self.root / "frontend"
        self.assertEqual(dev.project_role(["python", "-m", "uvicorn", "app.main:app"], backend, self.root), "API")
        self.assertEqual(dev.project_role(["python", "-m", "app.worker"], backend, self.root), "Worker")
        self.assertEqual(dev.project_role(["node", str(frontend / "node_modules/vite/bin/vite.js")], frontend, self.root), "Web")
        self.assertIsNone(dev.project_role(["python", "-m", "app.worker"], self.root / "other", self.root))
        self.assertIsNone(dev.project_role(["python", "unrelated.py"], backend, self.root))

    def test_stop_identifies_launcher_but_not_stop_command_itself(self):
        command = ["python", str(self.root / "scripts/dev.py")]
        self.assertEqual(dev.project_role(command, self.root / "backend", self.root), "Launcher")
        self.assertIsNone(dev.project_role(command + ["--stop"], self.root / "backend", self.root))
        self.assertIsNone(dev.project_role(command + ["--check"], self.root / "backend", self.root))

    def test_stop_does_not_terminate_unrelated_process(self):
        from types import SimpleNamespace

        own = SimpleNamespace(pid=10001, info={"cmdline": ["python", "-m", "app.worker"], "cwd": str(self.root / "backend")})
        other = SimpleNamespace(pid=10002, info={"cmdline": ["python", "-m", "app.worker"], "cwd": str(self.root / "other")})
        own.children = lambda **_: []
        own.terminate = unittest.mock.Mock()
        other.terminate = unittest.mock.Mock()
        with patch("psutil.process_iter", return_value=[own, other]), patch("psutil.wait_procs", return_value=([], [])):
            self.assertEqual(dev.stop_project(self.root), 0)
        own.terminate.assert_called_once()
        other.terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
