#!/bin/bash
# statuss — live watcher for CLI sessions (see README.md)
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSIONS="$ROOT/sessions"
PY="$(command -v python3 || echo python3)"
WATCH="$PY $ROOT/watch.py"

usage() {
  cat <<'TXT'
statuss — наглядач за CLI у терміналі

  statuss run <cli…>     запустити CLI в tmux із живою панеллю
  statuss                панель останньої активної сесії (q / Ctrl+C — вихід)
  statuss <name>         панель конкретної сесії
  statuss -1 [name]      один знімок (без оновлення)
  statuss -l             список сесій
  statuss --pid <pid>    стежити за довільним процесом
  statuss stop <name>    завершити сесію
  statuss clean          прибрати завершені сесії старші за 7 днів

Сесії: $ROOT/sessions/<name>/{session.log,pid,meta.json,exit}
TXT
}

sanitize() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_-' '-' | sed 's/^-*//; s/-*$//'
}

unique_session() {
  local base="$1" name="$1" n=2
  while [ -d "$SESSIONS/$name" ] && [ -f "$SESSIONS/$name/pid" ] && kill -0 "$(cat "$SESSIONS/$name/pid" 2>/dev/null)" 2>/dev/null; do
    name="${base}-${n}"; n=$((n + 1))
  done
  while tmux has-session -t "=$name" 2>/dev/null; do
    name="${base}-${n}"; n=$((n + 1))
  done
  printf '%s' "$name"
}

build_cmd() {
  local out="" arg
  for arg in "$@"; do out+="$(printf '%q' "$arg") "; done
  printf '%s' "$out"
}

cmd_run() {
  if [ "$#" -eq 0 ]; then usage; exit 1; fi
  local bin name sess cli watch_cmd
  bin="$1"
  name="$(sanitize "$(basename "$bin")")"
  [ -n "$name" ] || name="cli"
  mkdir -p "$SESSIONS"
  sess="$(unique_session "$name")"
  cli="$(build_cmd "$PY" "$ROOT/pty-run.py" --name "$sess" -- "$@")"
  watch_cmd="$(build_cmd bash "$ROOT/statuss.sh" "$sess")"

  if [ -n "${TMUX:-}" ]; then
    tmux new-window -n "$sess" -c "$PWD" "$cli" || return 1
    tmux split-window -v -l 14 -t ":$sess" "$watch_cmd"
    tmux select-pane -t ":$sess.0"
    echo "TMux-вікно «$sess» створено. Панель знизу оновлюється автоматично."
    return 0
  fi

  tmux new-session -d -s "$sess" -c "$PWD" "$cli" || return 1
  tmux split-window -v -l 14 -t "=$sess:" "$watch_cmd"
  tmux select-pane -t "=$sess:.0"
  tmux set-option -t "=$sess:" status off
  if [ -t 0 ] && [ -t 1 ]; then
    exec tmux attach -t "=$sess:"
  fi
  echo "Сесію «$sess» запущено у tmux. Підключитись: tmux attach -t $sess"
}

cmd_stop() {
  local name="${1:-}"
  if [ -z "$name" ]; then echo "Вкажи назву сесії (statuss -l)"; exit 1; fi
  local pid
  pid="$(cat "$SESSIONS/$name/pid" 2>/dev/null || true)"
  if [ -n "$pid" ]; then kill "$pid" 2>/dev/null && echo "Надіслано SIGTERM pid $pid ($name)"; else echo "Сесія «$name» не активна"; fi
  tmux kill-session -t "=$name" 2>/dev/null && echo "tmux-сесію закрито"
}

cmd_clean() {
  find "$SESSIONS" -mindepth 1 -maxdepth 1 -type d ! -name '*tmp*' -mtime +7 2>/dev/null |
    while read -r dir; do
      [ -f "$dir/pid" ] && continue
      rm -rf "$dir" && echo "видалено $dir"
    done
}

case "${1:-}" in
  ""|-h|--help|help) [ "${1:-}" = "" ] && exec $WATCH || usage ;;
  run|start) shift; cmd_run "$@" ;;
  -l|--list|list) exec $WATCH -l ;;
  -1|--once|--snapshot) shift; exec $WATCH -1 "$@" ;;
  --pid) shift; exec $WATCH --pid "$@" ;;
  stop|kill) shift; cmd_stop "$@" ;;
  clean|gc) cmd_clean ;;
  -*) exec $WATCH "$@" ;;
  *) exec $WATCH "$@" ;;
esac
