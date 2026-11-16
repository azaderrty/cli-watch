#!/usr/bin/env python3
"""Run a CLI inside a PTY, mirror its output to stdout and to a session log.

Usage: pty-run.py --name qwen -- qwen code
"""
import argparse
import errno
import fcntl
import json
import os
import pty
import select
import shutil
import signal
import struct
import sys
import termios
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(ROOT, "sessions")


def session_dir(name):
    os.makedirs(SESSIONS, exist_ok=True)
    path = os.path.join(SESSIONS, name)
    alive = os.path.exists(os.path.join(path, "pid"))
    if alive:
        base, n = name, 2
        while os.path.exists(os.path.join(SESSIONS, "%s-%d" % (base, n), "pid")):
            n += 1
        name = "%s-%d" % (base, n)
        path = os.path.join(SESSIONS, name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return name, path


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def set_winsize(fd, rows, cols):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="")
    ap.add_argument("--quiet", action="store_true", help="do not mirror to stdout")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        sys.exit("pty-run: no command given")

    name = args.name or os.path.basename(cmd[0])
    name, path = session_dir(name)
    log_path = os.path.join(path, "session.log")
    pid_path = os.path.join(path, "pid")
    exit_path = os.path.join(path, "exit")

    started = time.time()
    write_json(os.path.join(path, "meta.json"), {
        "name": name,
        "cmd": cmd,
        "cwd": os.getcwd(),
        "started": started,
        "started_human": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "log": log_path,
        "pid": os.getpid(),
        "log_pid": os.getpid(),
    })

    rows, cols = 40, 120
    try:
        sz = shutil.get_terminal_size()
        cols, rows = sz.columns, sz.lines
    except OSError:
        pass

    child, master = pty.fork()
    if child == 0:
        env = dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")
        try:
            os.execvpe(cmd[0], cmd, env)
        except OSError as exc:
            sys.stderr.write("pty-run: %s: %s\n" % (cmd[0], exc))
            os._exit(127)

    with open(pid_path, "w", encoding="ascii") as fh:
        fh.write("%d\n" % child)

    set_winsize(master, rows, cols)
    state = {"resize": False}

    def on_winch(signum, frame):
        state["resize"] = True

    def on_term(signum, frame):
        try:
            os.kill(child, signal.SIGTERM)
        except OSError:
            pass

    signal.signal(signal.SIGWINCH, on_winch)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)

    log = open(log_path, "ab", buffering=0)
    out = sys.stdout.buffer
    stdin_fd = sys.stdin.fileno() if sys.stdin.isatty() else None
    readable = [master] + ([stdin_fd] if stdin_fd is not None else [])

    try:
        while True:
            if state["resize"]:
                state["resize"] = False
                try:
                    sz = shutil.get_terminal_size()
                    set_winsize(master, sz.lines, sz.columns)
                except OSError:
                    pass
            try:
                ready, _, _ = select.select(readable, [], [], 1.0)
            except InterruptedError:
                continue
            if master in ready:
                try:
                    data = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                log.write(data)
                if not args.quiet:
                    try:
                        out.write(data)
                        out.flush()
                    except (BrokenPipeError, OSError):
                        pass
            if stdin_fd is not None and stdin_fd in ready:
                try:
                    data = os.read(stdin_fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    readable.remove(stdin_fd)
                    stdin_fd = None
                else:
                    try:
                        os.write(master, data)
                    except OSError:
                        pass
    finally:
        log.close()

    _, status = os.waitpid(child, 0)
    code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (
        os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status))
    try:
        os.remove(pid_path)
    except OSError:
        pass
    with open(exit_path, "w", encoding="ascii") as fh:
        fh.write("%d\n" % code)
    write_json(os.path.join(path, "meta.json"), {
        "name": name,
        "cmd": cmd,
        "cwd": os.getcwd(),
        "started": started,
        "started_human": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "finished": time.time(),
        "duration": round(time.time() - started, 1),
        "exit": code,
        "log": log_path,
        "log_pid": os.getpid(),
    })
    return code


if __name__ == "__main__":
    sys.exit(main())
