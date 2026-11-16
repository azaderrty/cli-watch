#!/usr/bin/env python3
"""Tail a session log: strip ANSI, return the last N visible lines as JSON."""
import argparse
import json
import os
import re
import sys

CSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]")
OSC = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
MISC = re.compile(rb"\x1b[()#][0-9A-Za-z]|\x1b[=>NOM78]|\x1b\][^\x07]*$")


def visible(data):
    data = OSC.sub(b"", data)
    data = CSI.sub(b"", data)
    data = MISC.sub(b"", data)
    text = data.decode("utf-8", "replace")
    lines = []
    for raw in re.split(r"\r\n|\r|\n", text):
        line = raw.rstrip()
        if not line.strip():
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("-n", "--lines", type=int, default=6)
    ap.add_argument("--size", type=int, default=65536)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = args.log
    if not os.path.exists(path):
        out = {"ok": False, "lines": [], "size": 0, "age": None, "total_lines": 0}
        print(json.dumps(out) if args.json else "")
        return 0

    size = os.path.getsize(path)
    age = max(0.0, __import__("time").time() - os.path.getmtime(path))
    want = args.lines + 40
    chunk = min(size, args.size)
    while True:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - chunk))
            data = fh.read()
        lines = visible(data)
        if len(lines) >= want or chunk >= size:
            break
        chunk = min(size, chunk * 4)

    lines = lines[-args.lines:] if args.lines > 0 else lines
    out = {
        "ok": True,
        "lines": lines,
        "size": size,
        "age": round(age, 1),
        "total_lines": len(lines),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for line in lines:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
