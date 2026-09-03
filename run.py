#!/usr/bin/env python3
"""Start every service with one command.

    python run.py backend     # main API + every agent backend
    python run.py frontend    # main web app + every agent UI
    python run.py             # both

Agents are discovered, not hard-coded: drop a folder under Agents/ containing an
agent.json and it joins the next run -- one agent or fifty, the command is the
same. Ports already in use are freed before anything starts, and the agent
registry the API reads is regenerated from the same manifests, so adding a
capability never means editing this file.
"""
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WIN = os.name == "nt"

MAIN = [
    {"name": "crag-api", "kind": "backend", "cwd": "backend", "port": 8000,
     "cmd": "{python} -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log"},
    {"name": "crag-web", "kind": "frontend", "cwd": "frontend", "port": 3000,
     "cmd": "npm run dev -- --port 3000"},
]


def manifests():
    return sorted(ROOT.glob("Agents/*/agent.json"))


def interpreter(cwd: Path) -> str:
    """A service's own venv if it has one; nothing here should share site-packages."""
    for candidate in (cwd / ".venv/Scripts/python.exe", cwd / ".venv/bin/python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def registry() -> Path:
    """Rewrite the API's agent registry from the agent manifests."""
    entries = {}
    for manifest in manifests():
        spec = json.loads(manifest.read_text(encoding="utf-8"))
        backend = spec.get("backend")
        if backend and spec.get("key"):
            entries[spec["key"]] = {"url": f"http://127.0.0.1:{backend['port']}",
                                    "audience": spec.get("audience", spec["key"] + "-agent")}
    path = ROOT / "deploy/agents.generated.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return path


def services(kinds):
    chosen = [dict(s, cwd=ROOT / s["cwd"]) for s in MAIN if s["kind"] in kinds]
    for manifest in manifests():
        spec = json.loads(manifest.read_text(encoding="utf-8"))
        for kind, suffix in (("backend", "api"), ("frontend", "web")):
            part = spec.get(kind)
            if part and kind in kinds:
                chosen.append({"name": f"{spec['name']}-{suffix}", "kind": kind,
                               "cwd": manifest.parent / part.get("cwd", "."),
                               "port": part["port"], "cmd": part["cmd"]})
    seen = {}
    for service in chosen:
        clash = seen.setdefault(service["port"], service["name"])
        if clash != service["name"]:
            raise SystemExit(f"Port {service['port']} is claimed by both {clash} and "
                             f"{service['name']}. Give one of them a different port.")
    return chosen


def listeners(port: int) -> set[int]:
    if WIN:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        return {int(parts[-1]) for line in out.splitlines()
                if "LISTENING" in line and len(parts := line.split()) >= 5
                and parts[1].endswith(f":{port}")}
    out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                         capture_output=True, text=True).stdout
    return {int(pid) for pid in out.split()}


def kill(pid: int) -> None:
    # System PIDs and this runner are never fair game, whatever netstat reports.
    if pid in (0, 4, os.getpid()):
        return
    if WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def free(port: int, name: str) -> None:
    for pid in listeners(port):
        print(f"[run] port {port} was held by PID {pid}; freeing it for {name}")
        kill(pid)
    for _ in range(30):
        if not listeners(port):
            return
        time.sleep(0.2)
    raise SystemExit(f"Port {port} is still occupied. Close the process using it and retry.")


def pump(name: str, process: subprocess.Popen) -> None:
    for line in process.stdout:
        print(f"[{name}] {line.rstrip()}", flush=True)


def command_for(service):
    """(command, needs_shell). Skipping the shell matters: with one, `Popen.pid` is a
    cmd/sh wrapper and the real server is a grandchild that outlives it, still holding
    whatever it opened. A stale uvicorn owning the embedded Qdrant lock binds no port,
    so a port sweep cannot see it, and the next boot dies on "Storage folder is already
    accessed by another instance". npm on Windows is a .cmd, which CreateProcess cannot
    exec, so that one keeps its shell -- it only ever holds a port, which the sweep does
    reach."""
    python = interpreter(service["cwd"])
    argv = [python if part == "@PY@" else part
            for part in shlex.split(service["cmd"].replace("{python}", "@PY@"))]
    program = argv[0] if Path(argv[0]).is_file() else (shutil.which(argv[0]) or argv[0])
    if Path(program).is_file() and not program.lower().endswith((".cmd", ".bat")):
        return [program, *argv[1:]], False
    return service["cmd"].format(python='"%s"' % python), True


def start(service, env):
    free(service["port"], service["name"])
    command, needs_shell = command_for(service)
    print(f"[run] {service['name']} :{service['port']}  ({service['cwd'].relative_to(ROOT)})")
    return subprocess.Popen(
        command, cwd=service["cwd"], env=env, shell=needs_shell, text=True, errors="replace",
        bufsize=1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {"start_new_session": True}))


def main() -> None:
    wanted = sys.argv[1:] or ["backend", "frontend"]
    unknown = set(wanted) - {"backend", "frontend"}
    if unknown:
        raise SystemExit(f"Usage: python run.py [backend|frontend]  (got {' '.join(unknown)})")

    env = {**os.environ, "AGENT_REGISTRY": str(registry()), "PYTHONUNBUFFERED": "1",
           "FORCE_COLOR": "1"}
    started, running = [], []
    try:
        for service in services(set(wanted)):
            process = start(service, env)
            threading.Thread(target=pump, args=(service["name"], process), daemon=True).start()
            started.append(service)
            running.append((service, process))
        print(f"[run] {len(running)} service(s) up. Ctrl+C stops all of them.")
        while running:
            for service, process in running:
                if process.poll() is not None:
                    print(f"[run] !! {service['name']} exited with code {process.returncode}")
                    running.remove((service, process))
                    break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print()
        print("[run] stopping services")
        for service, process in running:
            kill(process.pid)
        # Every service started, not just the ones still tracked: `shell=True` puts
        # a cmd/sh wrapper in between, and killing a wrapper that is already dead
        # finds no tree, so the real server survives holding its port. Clearing by
        # port cannot be orphaned that way -- and by here `running` is empty exactly
        # when the wrappers died on their own, which is the case that leaks.
        for service in started:
            for pid in listeners(service["port"]):
                kill(pid)


if __name__ == "__main__":
    main()
