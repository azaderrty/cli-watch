# cli-watch · `statuss`

Наглядач за CLI-процесами прямо в терміналі: показує, що саме робить
застосунок (CPU, IO, потоки, вивід), і каже прямо — працює, чекає чи завис.
Без root, без APK, без logcat.

```
statuss run qwen      # запустити CLI з живою панеллю в tmux
statuss               # панель останньої сесії (в іншому табі)
```

## Навіщо

Довгі CLI-сесії (AI-агенти, збирачі, тести) часто виглядають «мертвими»:
тихо, нічого не друкує. `statuss` показує об'єктивні ознаки життя і сам
ставить вердикт, тож не треба вгадувати, чи процес завис.

## Встановлення

Потрібні `tmux` і `python3` (обидва є в Alpine):

```sh
apk add tmux
git clone git@github.com:azaderrty/cli-watch.git ~/cli-watch
ln -sf ~/cli-watch/statuss.sh ~/.local/bin/statuss
```

`~/.local/bin` має бути в `PATH`.

## Команди

| Команда | Дія |
|---|---|
| `statuss run <cli…>` | запустити CLI в tmux: зверху CLI, знизу панель (14 рядків) |
| `statuss` | панель останньої активної сесії, оновлення 1 с |
| `statuss <name>` | панель конкретної сесії |
| `statuss -l` | список сесій: назва, стан, тривалість, команда |
| `statuss -1 [name]` | один знімок без оновлення (для логів/скриптів) |
| `statuss --pid <pid>` | стежити за довільним процесом (без тексту виводу) |
| `statuss stop <name>` | SIGTERM процесу + закрити tmux-сесію |
| `statuss clean` | видалити завершені сесії старші за 7 днів |

У панелі: `Ctrl+B ↑` — перейти на панель наглядача, `q` — вихід.
`Ctrl+B d` — відчепитись від tmux, `tmux attach -t <name>` — повернутись.

## Що показує панель

```
statuss · qwen · pid 4242 · 03:12 · 2026-11-16 18:47:46
────────────────────────────────────────────────────────────
STATE R — працює     CPU 42.1%   RSS 210.4 MB   THREADS 12 [3 проц.]
IO    read 1.2 MB/s   write 340.0 KB/s   syscalls 1204
OUT   останній вивід 2 с тому · 14.3 KB · 812 B/хв
PROC  node(4247) → rg(4251)
────────────────────────────────────────────────────────────
│ …останні 4 рядки виводу CLI…
────────────────────────────────────────────────────────────
STAT  ACTIVE — процес працює
WORK  main  M src/app.js  ?? notes.txt
DEV   Accessibility ON · foreground com.foxdebug.acodefree · …
панель: Ctrl+B ↑ · q — вихід
```

- **STATE / CPU / RSS / THREADS** — агрегат по всьому дереву процесів сесії
  (скрипти на кшталт `qwen` запускають `node` дочірнім процесом).
- **IO** — швидкість читання/запису з `/proc/<pid>/io` (не мережа).
- **OUT** — розмір логу, час від останнього виводу, швидкість.
- **PROC** — дочірні процеси (хто насправді працює).
- **STAT** — вердикт:
  - `ACTIVE` — є новий вивід або CPU > 1 %;
  - `WAITING` — тихо 15–90 с при низькому CPU (чекає вводу або відповіді мережі);
  - `STALLED` — 90 с без виводу і CPU < 1 %;
  - `DONE` — процес завершився (`exit N`).
- **WORK** — `git status` робочої теки сесії.
- **DEV** — необов'язково: стан SysAdmin і поточний екран (див. нижче).

## Як це працює

1. `pty-run.py` запускає CLI в псевдотерміналі (PTY) і дублює весь потік у
   `sessions/<name>/session.log`, поруч пише `pid`, `meta.json`, `exit`.
   PTY потрібен, щоб інтерактивні CLI (TUI) поводились як у справжньому терміналі.
2. `watch.py` раз на секунду читає `/proc/<pid>/{stat,io}`, дерево процесів,
   хвіст логу (`logtail.py` знімає ANSI) і малює екран.
3. `statuss.sh` — диспетчер команд і запуск tmux.

## Блок «Планшет» (опційно)

Якщо на пристрої запущений **SysAdmin** з локальним API `127.0.0.1:8787`,
панель раз на 3 с питає `/status` і `/text` та показує foreground-застосунок і
текст екрана. SysAdmin вимкнений/недоступний — блок пише `n/a`, решта працює.

Вимкнути повністю: `STATUSS_NO_DEVICE=1 statuss`.

## Налаштування

| Змінна | Типово | Значення |
|---|---|---|
| `STATUSS_WAIT` | `15` | секунд тиші до вердикту `WAITING` |
| `STATUSS_STALE` | `90` | секунд тиші до вердикту `STALLED` |
| `STATUSS_HOLD` | `8` | секунд показувати `DONE` перед виходом |
| `STATUSS_NO_DEVICE` | — | `1` — не звертатись до SysAdmin |
| `NO_COLOR` | — | вимкнути кольори |

## Обмеження (чесно)

- Показує **CLI-сесії**, а не весь планшет. Інші застосунки, натискання та
  системні події — тільки через Accessibility/APK (окрема фаза).
- Текст виводу видно лише для сесій, запущених через `statuss run`
  (вони пишуть лог). Для `statuss --pid` доступні лише CPU/IO/стан.
- Мережу (сокети, трафік) показати неможливо: Android забороняє `/proc/net/*`
  і `netstat` без root. `logcat`/`dumpsys` зі звичайного застосунку теж закриті.
- Логи сесій можуть містити конфіденційні дані (вивід CLI). Тека `sessions/`
  у `.gitignore`; чисть `statuss clean` або вручну.

## Видалення

```sh
rm -f ~/.local/bin/statuss
rm -rf ~/cli-watch
apk del tmux
```

## English summary

`cli-watch` is a small, root-free CLI session monitor for Android/Termux-like
environments. `statuss run <cli…>` starts the tool inside a PTY in tmux with a
14-line live dashboard; `statuss` attaches a dashboard from any other terminal.
It aggregates `/proc/<pid>/{stat,io}` over the process tree, tails the session
log (ANSI-stripped) and prints a verdict: ACTIVE / WAITING / STALLED / DONE.
Sockets and system-wide logs are unavailable without root. Sessions live in
`sessions/<name>/` and are gitignored.

MIT license.
