"""One-terminal development supervisor; never stop services it did not start."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRINT_LOCK = threading.Lock()


def say(message):
    with PRINT_LOCK:
        print(message, flush=True)


def http_body(url):
    # Local development probes must not pass through a system HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=2) as response:
        return response.read(2 * 1024 * 1024).decode("utf-8")


def api_ready():
    try:
        return json.loads(http_body("http://127.0.0.1:8010/openapi.json"))["info"]["title"] == "知衡 · RAG Knowledge Assistant"
    except (OSError, ValueError, KeyError):
        return False


def frontend_ready():
    try:
        return "<title>知衡 · Knowledge OS</title>" in http_body("http://127.0.0.1:5173/")
    except OSError:
        return False


def port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.3):
            return True
    except OSError:
        return False


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("已有一键启动窗口在运行，请回到该窗口操作。") from None
        return self

    def __exit__(self, *_):
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


@dataclass
class Service:
    name: str
    command: list[str]
    cwd: Path
    ready: object
    port: int | None = None
    process: subprocess.Popen | None = None
    reader: threading.Thread | None = None
    reused: bool = False


class Supervisor:
    def __init__(self, services, log_dir, timeout=60):
        self.services = services
        self.log_dir = Path(log_dir)
        self.timeout = timeout

    def inspect(self, service):
        if service.ready():
            say(f"[{service.name}] 已运行，复用现有服务；退出时保留该服务。")
            service.reused = True
        elif service.port and port_in_use(service.port):
            raise RuntimeError(f"端口 {service.port} 已被其他服务占用或当前服务未就绪，请先检查占用者。未结束任何已有进程。")

    def start(self, service):
        if service.reused:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        service.process = subprocess.Popen(service.command, cwd=service.cwd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}, **kwargs)

        def relay():
            with (self.log_dir / (service.name.lower() + ".log")).open("w", encoding="utf-8") as log:
                for line in service.process.stdout:
                    log.write(line)
                    log.flush()
                    say(f"[{service.name}] {line.rstrip()}")

        service.reader = threading.Thread(target=relay, daemon=True)
        service.reader.start()
        say(f"[{service.name}] 启动中，PID={service.process.pid}")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if service.process.poll() is not None:
                raise RuntimeError(f"{service.name} 启动失败，退出码 {service.process.returncode}；请查看上方日志。")
            if service.ready():
                say(f"[{service.name}] 已就绪")
                return
            time.sleep(.3)
        raise RuntimeError(f"{service.name} 启动超时；请检查数据库、Redis、Qdrant 等依赖及上方日志。")

    def shutdown(self):
        import psutil

        owned = [s for s in reversed(self.services) if s.process is not None]
        descendants = []
        # Signal all owned children before waiting, so shutdown time is bounded.
        for service in owned:
            process = service.process
            if process.poll() is None:
                with contextlib.suppress(psutil.Error):
                    descendants.extend(psutil.Process(process.pid).children(recursive=True))
                say(f"[{service.name}] 正在停止…")
                with contextlib.suppress(OSError):
                    if os.name == "nt":
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(process.pid, signal.SIGINT)
        deadline = time.monotonic() + 10
        for service in owned:
            process = service.process
            try:
                process.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if service.reader:
                service.reader.join(timeout=2)
            if process.stdout and (not service.reader or not service.reader.is_alive()):
                process.stdout.close()
        # Vite/esbuild or OCR subprocesses can outlive an abruptly failed parent.
        # These objects identify only descendants of processes created above.
        for child in descendants:
            with contextlib.suppress(psutil.Error):
                child.terminate()
        _, alive = psutil.wait_procs(descendants, timeout=1)
        for child in alive:
            with contextlib.suppress(psutil.Error):
                child.kill()

    def run(self):
        try:
            # Inspect the entire plan before starting any process.
            for service in self.services:
                self.inspect(service)
            for service in self.services:
                self.start(service)
            say("\n前端 http://localhost:5173   API http://127.0.0.1:8010/docs")
            say("按 Ctrl+C 停止本窗口启动的服务；复用的服务、Docker 和 Ollama 保持运行。\n")
            while True:
                for service in self.services:
                    if service.process is not None and service.process.poll() is not None:
                        raise RuntimeError(f"{service.name} 意外退出（{service.process.returncode}），停止其余本次启动的服务。")
                time.sleep(.5)
        finally:
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                self.shutdown()
            finally:
                signal.signal(signal.SIGINT, previous)


def project_role(command, cwd, root):
    """Match this checkout, rather than every Python/Node process on the PC."""
    if not command or not cwd:
        return None
    directory = Path(cwd).resolve()
    if directory == (root / "backend").resolve():
        if "app.worker" in command:
            return "Worker"
        if "app.main:app" in command and any("uvicorn" in Path(arg).name for arg in command):
            return "API"
        if "--stop" not in command and "--check" not in command:
            target = (root / "scripts/dev.py").resolve()
            for argument in command[1:]:
                if argument.replace("\\", "/") in {"scripts/dev.py", "./scripts/dev.py", "dev.py"} or Path(argument).resolve() == target:
                    return "Launcher"
    if directory == (root / "frontend").resolve() and any(Path(arg).name in {"vite.js", "vite"} for arg in command):
        return "Web"
    return None


def stop_project(root):
    import psutil

    targets = {}
    for process in psutil.process_iter(["pid", "cmdline", "cwd"]):
        if process.pid == os.getpid():
            continue
        try:
            role = project_role(process.info["cmdline"], process.info["cwd"], root)
            if role:
                targets[process.pid] = (process, role)
                for child in process.children(recursive=True):
                    targets.setdefault(child.pid, (child, "子进程"))
        except (psutil.Error, OSError):
            continue
    if not targets:
        say("未发现此项目正在运行的开发服务。")
        return 0
    for process, role in targets.values():
        try:
            process.terminate()
            say(f"[{role}] 停止 PID={process.pid}")
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            say(f"[{role}] 无权停止 PID={process.pid}，请使用启动该服务的账号。")
    _, alive = psutil.wait_procs([process for process, _ in targets.values()], timeout=5)
    for process in alive:
        with contextlib.suppress(psutil.Error):
            process.kill()
    _, alive = psutil.wait_procs(alive, timeout=3)
    if alive:
        say("仍有进程未能停止：" + ", ".join(str(process.pid) for process in alive))
        return 1
    say("此项目的前端、后端和 Worker 已停止。")
    return 0


def build_plan(root, python):
    try:
        import psutil
    except ImportError:
        raise RuntimeError("缺少开发依赖 psutil，请在项目根目录执行 python -m pip install -e './backend[dev]'。") from None
    sys.path.insert(0, str(root / "backend"))
    from app.config import get_settings
    from app.db import Database
    from app.models import AdmissionSlot, utcnow
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    settings = get_settings()
    node = shutil.which("node")
    vite = root / "frontend/node_modules/vite/bin/vite.js"
    if not node or not vite.is_file():
        raise RuntimeError("缺少 Node.js 或前端依赖，请安装 Node.js，并在 frontend 目录执行 npm ci。")

    def worker_ready():
        database = Database(settings)
        try:
            with database.session() as db:
                keys = db.scalars(select(AdmissionSlot.key).where(
                    AdmissionSlot.key.like(f"worker:{socket.gethostname()}:%"), AdmissionSlot.expires_at > utcnow())).all()
                for key in keys:
                    try:
                        process = psutil.Process(int(key.split(":")[2]))
                        command = process.cmdline()
                        if "app.worker" in command and Path(process.cwd()).resolve() == (root / "backend").resolve():
                            return True
                    except (psutil.Error, ValueError, IndexError):
                        continue
                return False
        except SQLAlchemyError:
            return False
        finally:
            database.engine.dispose()

    services = [Service("API", [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8010"],
                        root / "backend", api_ready, 8010)]
    if settings.redis_url:
        services.append(Service("Worker", [python, "-m", "app.worker"], root / "backend", worker_ready))
    else:
        say("[Worker] 未启用 Redis，使用 API 内置任务线程。")
    services.append(Service("Web", [node, str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
                            root / "frontend", frontend_ready, 5173))
    return services


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Only inspect dependencies and existing services")
    mode.add_argument("--stop", action="store_true", help="Stop this checkout's development services, even if their terminal was closed")
    args = parser.parse_args()
    os.chdir(ROOT / "backend")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.stop:
            return stop_project(ROOT)
        services = build_plan(ROOT, sys.executable)
        supervisor = Supervisor(services, ROOT / "artifacts/dev")
        if args.check:
            for service in services:
                supervisor.inspect(service)
                if not service.reused:
                    say(f"[{service.name}] 尚未运行，一键启动时将启动。")
            return 0
        with InstanceLock(ROOT / "artifacts/dev/launcher.lock"):
            supervisor.run()
    except KeyboardInterrupt:
        say("\n本次启动的服务已停止。")
        return 0
    except (RuntimeError, OSError) as error:
        say(f"\n启动未完成：{error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
