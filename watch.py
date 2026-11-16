#!/usr/bin/env python3
"""Live dashboard for a CLI session started by pty-run.py."""
import argparse
import json
import os
import select
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(ROOT, "sessions")
CLK_TCK = 100
PAGE = 4096
STALE_AFTER = float(os.environ.get("STATUSS_STALE", "90") or 90)
WAIT_AFTER = float(os.environ.get("STATUSS_WAIT", "15") or 15)
API = "http://127.0.0.1:8787"

STATE_NAMES = {
    "R": "R — працює",
    "S": "S — спить / чекає",
    "D": "D — чекає диск",
    "T": "T — зупинений",
    "t": "t — трасування",
    "Z": "Z — зомбі",
    "I": "I — idle",
    "X": "X — завершується",
}


COLOR = True


def c(text, code, on):
    return "\033[%sm%s\033[0m" % (code, text) if (on and COLOR) else text


def clip(text, width):
    text = " ".join(str(text).split())
    if width <= 1:
        return ""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def read_stat(pid):
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    rp = data.rfind(")")
    comm = data[data.find("(") + 1 : rp]
    rest = data[rp + 2 :].split()
    try:
        return {
            "pid": pid,
            "comm": comm,
            "state": rest[0],
            "ppid": int(rest[1]),
            "utime": int(rest[11]),
            "stime": int(rest[12]),
            "threads": int(rest[17]),
            "start": int(rest[19]),
            "rss": int(rest[21]) * PAGE,
        }
    except (IndexError, ValueError):
        return None


def read_io(pid):
    out = {}
    try:
        with open("/proc/%d/io" % pid) as fh:
            for line in fh:
                key, _, val = line.partition(":")
                out[key.strip()] = int(val.strip())
    except (OSError, ValueError):
        return None
    return out


def uptime():
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError):
        return 0.0


def human_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0


def human_time(sec):
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


def descendants(root_pid):
    procs = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        info = read_stat(int(entry))
        if info:
            procs[info["pid"]] = info
    kids = {}
    for info in procs.values():
        kids.setdefault(info["ppid"], []).append(info)
    found, queue = [], [root_pid]
    while queue:
        for child in kids.get(queue.pop(0), []):
            found.append(child)
            queue.append(child["pid"])
    return found[:6]


def git_info(cwd):
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        res = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain=v1", "-b"],
            capture_output=True, text=True, timeout=1.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    lines = [ln for ln in res.stdout.splitlines() if ln]
    branch, dirty = "", []
    for line in lines:
        if line.startswith("##"):
            branch = line[2:].strip()
        else:
            dirty.append(line)
    return {"branch": branch, "dirty": dirty}


def http_json(path, timeout=0.8):
    try:
        with urllib.request.urlopen(API + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def latest_session():
    if not os.path.isdir(SESSIONS):
        return None
    best, best_key = None, None
    for name in os.listdir(SESSIONS):
        path = os.path.join(SESSIONS, name)
        if not os.path.isdir(path):
            continue
        pid_file = os.path.join(path, "pid")
        log = os.path.join(path, "session.log")
        key = (os.path.exists(pid_file), os.path.getmtime(log) if os.path.exists(log) else 0)
        if best_key is None or key > best_key:
            best, best_key = name, key
    return best


def load_meta(path):
    try:
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def list_sessions():
    rows = []
    if not os.path.isdir(SESSIONS):
        return rows
    for name in sorted(os.listdir(SESSIONS)):
        path = os.path.join(SESSIONS, name)
        if not os.path.isdir(path):
            continue
        meta = load_meta(path)
        pid = None
        try:
            with open(os.path.join(path, "pid")) as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            pass
        info = read_stat(pid) if pid else None
        if info:
            status = "ACTIVE pid %d" % pid
            started = meta.get("started") or (time.time() - (uptime() - info["start"] / CLK_TCK))
            ago = human_time(time.time() - started)
        else:
            code = meta.get("exit")
            status = "DONE exit %s" % code if code is not None else "—"
            ago = "—"
        rows.append((name, status, ago, " ".join(meta.get("cmd") or [])))
    return rows


class Sampler:
    """Aggregates CPU/IO over the session process tree."""

    def __init__(self, pid):
        self.pid = pid
        self.prev = None
        self.first_seen = time.time()
        self.idle_since = time.time()
        self.last_log_size = None

    def tree(self):
        return [self.pid] + [k["pid"] for k in descendants(self.pid)]

    def sample(self):
        pids = self.tree()
        stats = [x for x in (read_stat(p) for p in pids) if x]
        now = time.time()
        if not stats:
            return None, None, 0.0, 0.0, 0.0, now
        utime = sum(x["utime"] for x in stats)
        stime = sum(x["stime"] for x in stats)
        io = {"rchar": 0, "wchar": 0, "syscr": 0, "syscw": 0}
        for p in pids:
            part = read_io(p)
            if part:
                for key in io:
                    io[key] += part.get(key, 0)
        cpu = read_rate = write_rate = 0.0
        if self.prev:
            dt = max(1e-6, now - self.prev["t"])
            cpu = ((utime + stime) - self.prev["cpu"]) / CLK_TCK / dt * 100
            read_rate = (io["rchar"] - self.prev["rchar"]) / dt
            write_rate = (io["wchar"] - self.prev["wchar"]) / dt
        self.prev = {"t": now, "cpu": utime + stime, "rchar": io["rchar"], "wchar": io["wchar"]}
        view = dict(stats[0] if stats[0]["pid"] == self.pid else read_stat(self.pid) or stats[0])
        view["threads"] = sum(x["threads"] for x in stats)
        view["rss"] = sum(x["rss"] for x in stats)
        view["procs"] = len(stats)
        return view, io, cpu, read_rate, write_rate, now


def draw(session, name, opts, sampler, dev_cache, snap):
    width = max(40, min(160, shutil.get_terminal_size((100, 30)).columns))
    if snap:
        stat, io, cpu, read_rate, write_rate, now = snap
    else:
        stat, io, cpu, read_rate, write_rate, now = None, None, 0.0, 0.0, 0.0, time.time()

    meta = load_meta(session) if session else {}
    log = os.path.join(session, "session.log") if session else None
    log_size, log_age, tail = 0, None, []
    if log and os.path.exists(log):
        log_size = os.path.getsize(log)
        log_age = max(0.0, now - os.path.getmtime(log))
        try:
            res = subprocess.run(
                [sys.executable, os.path.join(ROOT, "logtail.py"), log, "-n", "4", "--json"],
                capture_output=True, text=True, timeout=3,
            )
            tail = json.loads(res.stdout or "{}").get("lines") or []
        except Exception:
            tail = []

    # idle tracking
    if sampler:
        grew = sampler.last_log_size is not None and log_size != sampler.last_log_size
        sampler.last_log_size = log_size
        if grew or cpu > 1.0 or read_rate + write_rate > 4096:
            sampler.idle_since = now
        quiet = now - sampler.idle_since
    else:
        quiet = None

    if stat:
        started = meta.get("started") or (sampler.first_seen if sampler else now)
        elapsed = max(0.0, now - started)
        state = STATE_NAMES.get(stat["state"], stat["state"])
        head = "statuss · %s · pid %d · %s · %s" % (name or "?", stat["pid"], human_time(elapsed), meta.get("started_human", ""))
        extra = " [%d проц.]" % stat["procs"] if stat.get("procs", 1) > 1 else ""
        row1 = "STATE %-18s CPU %5.1f%%   RSS %8s   THREADS %d%s" % (
            state, cpu, human_bytes(stat["rss"]), stat["threads"], extra)
        row2 = "IO    read %9s/s   write %9s/s   syscalls %s" % (
            human_bytes(read_rate), human_bytes(write_rate),
            "%d" % (io.get("syscr", 0) + io.get("syscw", 0)) if io else "n/a")
        if log_age is None:
            row3 = "OUT   лог недоступний"
        else:
            rate = log_size / max(1e-6, elapsed) * 60
            row3 = "OUT   останній вивід %.0f с тому · %s · %s/хв" % (log_age, human_bytes(log_size), human_bytes(rate))
        kids = descendants(stat["pid"])
        kid_line = "PROC  " + (" → ".join("%s(%s)" % (k["comm"], k["pid"]) for k in kids) if kids else "без дочірніх")
        if quiet is None:
            verdict, color = "ACTIVE", "32"
        elif quiet >= STALE_AFTER and cpu < 1.0:
            verdict, color = "STALLED — %.0f с без виводу і CPU, схоже на зависання" % quiet, "31"
        elif quiet >= WAIT_AFTER:
            verdict, color = "WAITING — %.0f с тихо, чекає вводу або відповіді" % quiet, "33"
        else:
            verdict, color = "ACTIVE — процес працює", "32"
    else:
        exit_code = meta.get("exit")
        head = "statuss · %s · завершено" % (name or "?")
        row1 = "PROC  процес завершився" + (" · exit %s" % exit_code if exit_code is not None else "")
        row2 = ""
        row3 = "OUT   %s" % (human_bytes(log_size) if log_size else "без логу")
        kid_line = "PROC  —"
        elapsed = meta.get("duration") or 0
        verdict, color = ("DONE — exit %s" % exit_code) if exit_code is not None else "DONE", "36"

    lines = [c(clip(head, width), "1;36", True),
             c("─" * width, "90", True),
             clip(row1, width), clip(row2, width), clip(row3, width), clip(kid_line, width),
             ]
    if tail or session:
        lines.append(c("─" * width, "90", True))
    if tail:
        for line in tail:
            lines.append(clip("│ " + line, width))
    elif session:
        lines.append(clip("│ (виводу ще немає)", width))
    lines.append(c("─" * width, "90", True))
    lines.append(c(clip("STAT  " + verdict, width), color, True))
    git = git_info(meta.get("cwd"))
    if git:
        dirty = git["dirty"]
        shown = ", ".join(d.replace("\n", " ")[:28] for d in dirty[:3])
        more = " +%d" % (len(dirty) - 3) if len(dirty) > 3 else ""
        lines.append(clip("WORK  %s  %s%s" % (git["branch"] or "?", shown, more), width))
    if opts.device and now - dev_cache.get("t", 0) > 3:
        dev_cache["t"] = now
        dev_cache["status"] = http_json("/status")
        dev_cache["text"] = http_json("/text")
    if opts.device:
        st = dev_cache.get("status")
        if st:
            fg = st.get("foreground") or "?"
            scr = ((dev_cache.get("text") or {}).get("text") or "").replace("\n", " ")
            lines.append(clip("DEV   %s · foreground %s · %s" % (
                "Accessibility ON" if st.get("accessibility") else "Accessibility OFF", fg, clip(scr, width // 2)), width))
        else:
            lines.append(clip("DEV   SysAdmin недоступний (127.0.0.1:8787)", width))
    if opts.hints:
        lines.append(c(clip("панель: Ctrl+B ↑ · q — вихід", width), "90", True))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?")
    ap.add_argument("-1", "--once", action="store_true")
    ap.add_argument("-l", "--list", action="store_true")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--no-device", action="store_true")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    if args.list:
        rows = list_sessions()
        if not rows:
            print("Немає сесій. Запусти: statuss run <cli…>")
            return 0
        print("%-16s %-18s %-8s %s" % ("СЕСІЯ", "СТАН", "ТРИВАЛІСТЬ", "КОМАНДА"))
        for name, status, ago, cmd in rows:
            print("%-16s %-18s %-8s %s" % (name, status, ago, cmd))
        return 0

    tty = sys.stdout.isatty()
    global COLOR
    COLOR = tty and not os.environ.get("NO_COLOR")
    interactive = tty and not args.once
    opts = type("O", (), {})()
    opts.device = tty and not args.no_device and not os.environ.get("STATUSS_NO_DEVICE")
    opts.hints = interactive
    opts.hold = float(os.environ.get("STATUSS_HOLD", "8") or 8)

    pid = args.pid
    session = None
    name = args.session
    if pid:
        name = name or "pid-%d" % pid
    else:
        if name:
            session = os.path.join(SESSIONS, name)
            if not os.path.isdir(session):
                print("Немає сесії «%s». Подивись: statuss -l" % name)
                return 1
        else:
            name = latest_session()
            if not name:
                print("Немає активних сесій. Запусти: statuss run <cli…>")
                return 1
            session = os.path.join(SESSIONS, name)
        try:
            with open(os.path.join(session, "pid")) as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            pid = None

    if pid:
        sampler = Sampler(pid)
    else:
        sampler = None

    dev_cache = {}
    stdin_fd = sys.stdin.fileno() if sys.stdin.isatty() else None
    attrs = None
    if interactive and stdin_fd is not None:
        try:
            import termios, tty
            attrs = termios.tcgetattr(stdin_fd)
            tty.setcbreak(stdin_fd)
        except Exception:
            attrs = None

    dead_since = None
    if sampler:
        sampler.sample()
        time.sleep(0.3)

    try:
        while True:
            snap = sampler.sample() if sampler else None
            lines = draw(session, name, opts, sampler, dev_cache, snap)
            if interactive:
                sys.stdout.write("\033[H\033[2J" + "\n".join(lines) + "\n")
                sys.stdout.flush()
            else:
                print("\n".join(lines))
                return 0

            quit_now = False
            if stdin_fd is not None:
                ready, _, _ = select.select([stdin_fd], [], [], args.interval)
                if ready:
                    key = os.read(stdin_fd, 1)
                    if key in (b"q", b"Q", b"\x03", b"\x1b"):
                        quit_now = True
            else:
                time.sleep(args.interval)
            if quit_now:
                break
            if dead_since is not None and time.time() - dead_since > opts.hold:
                break
            if sampler and read_stat(sampler.pid) is None:
                if dead_since is None:
                    dead_since = time.time()
                    sampler = None
                    snap = None
                    lines = draw(session, name, opts, None, dev_cache, None)
                    sys.stdout.write("\033[H\033[2J" + "\n".join(lines) + "\n")
                    sys.stdout.flush()
                continue
    except KeyboardInterrupt:
        pass
    finally:
        if attrs is not None:
            try:
                import termios
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, attrs)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
