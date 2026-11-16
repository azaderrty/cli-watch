# cli-watch — нотатки для AI-агентів

Деталі — у `README.md`. Коротко:

- Мова інтерфейсу — українська, код і назви — англійською.
- `statuss.sh` — диспетчер (bash), `pty-run.py` — запам'ятовує сесію в PTY,
  `watch.py` — малює панель, `logtail.py` — ANSI-очищений хвіст логу.
- Нічого не пише поза `sessions/` (gitignored) і `~/.local/bin/statuss` при встановленні.
- Не додавати залежностей, крім `tmux` + `python3`; не використовувати root/ADB.
- Обмеження Android (не ламати): `/proc/net/*`, `logcat`, `dumpsys` недоступні.
- Перевірка після змін:
  `python3 -m py_compile *.py && bash -n statuss.sh && statuss -1`
  і живий тест: `statuss run sh -c 'echo hi; sleep 3'`.
