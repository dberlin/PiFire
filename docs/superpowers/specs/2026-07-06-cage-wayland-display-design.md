# Run Qt displays under cage (Wayland kiosk)

## Problem

The display process is launched bare by supervisor:

```ini
# auto-install/supervisor/display.conf
command=/usr/local/bin/pifire/.venv/bin/python /usr/local/bin/pifire/display_process.py
```

`display_process.py` loads the configured display module (`settings['modules']['display']`)
and renders. The SPI displays (`st7789_*`, `ili9341_*`, `protoflex`, ...) draw
straight to hardware, and the pygame displays (`dsi_800x480t`, `pygame_*`, ...)
use the framebuffer — none of these need a display server.

The QtQuick/PySide6 backends (`qtquick_flex`, `qtquick_dsi_1280x720t`) are
different: they build a `QGuiApplication` in a spawned child (`display/qtapp.py`)
and need a real Wayland session to render onto. Run bare, Qt falls back to
whatever platform plugin it can find (eglfs/linuxfb) and behaves inconsistently.
We want the Qt backends to run inside **cage**, a single-application Wayland
kiosk compositor, while leaving every other display exactly as it is today.

## Goal

Launch the display process inside `cage` **only** when the configured display is
a Qt backend; otherwise launch bare, unchanged. Provide the system prerequisites
(cage, seat access) via the installers so a fresh install works turnkey.

Non-goals: changing how any display *renders*; touching `qtapp.py` /
`qtbackend.py` or the QML; altering the non-Qt launch paths; supporting a
compositor other than cage.

## Detection rule

A display is a Qt backend iff `settings['modules']['display']` starts with
`qtquick_`. Today that is `qtquick_flex` and `qtquick_dsi_1280x720t`; any future
Qt backend follows the same module-naming convention. The check is a plain
string comparison — it must **not** import the display module, preserving the
existing invariant that PySide6 is never loaded in the display *parent* process
(Qt lives only in the child spawned by `display/qtapp.py`).

## Architecture

### 1. Launcher shim — `display_launch.py` (new, repo root)

Supervisor calls this instead of `display_process.py` directly. It:

1. Reads settings via `common.read_settings()`.
2. Computes the child argv: `[sys.executable, "display_process.py"]`.
3. If `settings['modules']['display']` starts with `qtquick_`:
   - Ensures `XDG_RUNTIME_DIR` (see below).
   - Sets `QT_QPA_PLATFORM=wayland` in the environment.
   - Wraps argv as `["cage", "-s", "--", *child_argv]`.
4. `os.execvp(argv[0], argv)` — replace the process image.

Using `execvp` (not `subprocess`) keeps supervisor supervising a single process,
so `stopasgroup=true` still tears down cage + Qt cleanly on stop/restart. When
cage is used, cage's child is `display_process.py`, which stays alive running the
`DisplayFeeder` loop, so cage stays up for the life of the display.

The shim lives at repo root (not under `display/`, a package) so it can be run as
a plain script like `display_process.py`, and it deliberately imports nothing
from `display/`.

### 2. `XDG_RUNTIME_DIR` handling (standard `/run/user/<UID>`)

cage needs `XDG_RUNTIME_DIR` to create its Wayland socket. The launcher:

- Uses an existing `XDG_RUNTIME_DIR` from the environment if one is set (e.g. a
  real login session) — never overrides it.
- Otherwise defaults to `/run/user/<uid>` where `uid = os.getuid()`. Supervisor
  runs the display process as **root** (the supervisor configs set no `user=`,
  and the system supervisord runs as root), so this is normally `/run/user/0`.
- Ensures the directory exists: `os.makedirs(path, mode=0o700, exist_ok=True)`
  then `os.chmod(path, 0o700)`. systemd-logind only auto-creates
  `/run/user/<UID>` for actual login sessions; the root supervisor process is not
  one, so the launcher creates it if missing.

Only set for the Qt path; the bare path leaves the environment untouched.

### 3. `auto-install/supervisor/display.conf`

Point the command at the launcher:

```ini
command=/usr/local/bin/pifire/.venv/bin/python /usr/local/bin/pifire/display_launch.py
```

No `environment=` line is needed — `QT_QPA_PLATFORM` and `XDG_RUNTIME_DIR` are
set by the launcher, only on the Qt path. The `legacy/display.conf`
(non-venv `bin/python`) gets the same command change against its interpreter path
for parity.

### 4. Installers — cage + seat access (all three)

`auto-install/install.sh` (apt), `auto-install/pifire-dietpi.sh` (apt), and
`auto-install/install-fedora.sh` (dnf) each:

- Add `cage` and `seatd` to the package install invocation for that OS.
- Enable the seat daemon: `systemctl enable --now seatd`.
- Add the pifire group and user to the `video`, `input`, `render`, and `seat`
  groups (alongside the existing `groupadd pifire` / `usermod -a -G pifire`
  block in `install.sh`).

Since the display process runs as **root**, seat/group membership is largely
redundant for the shipped configuration (root already has DRM/input access, and
cage's libseat can use its built-in backend as root). It is included as
belt-and-suspenders so the setup keeps working if the display process is ever
run as an unprivileged user.

## cage invocation

`cage -s -- <cmd>`: `-s` allows VT switching (lets the console be reclaimed);
the app after `--` is the single client cage runs fullscreen. cage exits when its
client exits, and supervisor's `autorestart=true` brings the whole thing back.

## Error handling

- **cage not installed** on a Qt display: `execvp` raises `FileNotFoundError`;
  the launcher logs a clear error naming the missing `cage` binary and exits
  non-zero. Supervisor retries per `startretries`. (A fresh install has cage via
  §4; this path only bites a hand-configured system.)
- **Non-Qt display**: launcher is a straight passthrough to
  `python display_process.py`; behavior is byte-for-byte the current behavior.
- **`XDG_RUNTIME_DIR` unwritable**: `makedirs`/`chmod` errors are logged with the
  offending path and the launcher exits non-zero rather than starting cage
  against a socket dir that won't work.

## Testing

- **Detection unit test**: a helper `build_launch_argv(settings, env)` (the pure
  core of the launcher) returns the bare argv for non-`qtquick_` modules and the
  `["cage", "-s", "--", ...]` argv with `QT_QPA_PLATFORM=wayland` for
  `qtquick_*` modules. Table-driven over `none`, `st7789_240x320`,
  `dsi_800x480t`, `qtquick_flex`, `qtquick_dsi_1280x720t`.
- **XDG_RUNTIME_DIR test**: existing value in env is preserved; absent value
  defaults to `/run/user/<uid>`. Use a fake env dict / monkeypatched `os.getuid`;
  do not touch the real filesystem in the unit test.
- No test spawns cage or Qt — the launcher's job is argv/env construction, which
  is what the tests pin.

## Files

- `display_launch.py` — new launcher shim (repo root).
- `tests/test_display_launch.py` — new; detection + env tests.
- `auto-install/supervisor/display.conf` — command → launcher.
- `auto-install/supervisor/legacy/display.conf` — command → launcher (legacy interp).
- `auto-install/install.sh` — cage + seatd packages (apt), enable seatd, group adds (extends the existing `groupadd pifire`/`usermod` block at ~L249).
- `auto-install/pifire-dietpi.sh` — cage + seatd packages (apt), enable seatd, group adds (extends the block at ~L158).
- `auto-install/install-fedora.sh` — cage + seatd packages (dnf), enable seatd, group adds (extends the block at ~L169).
