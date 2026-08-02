# Sudo call sites and the umask they run under — survey

**Date:** 2026-08-01
**Status:** Survey complete. One preventive change proposed and deliberately NOT made.

## Why this was looked at

`board-config.py` runs as `sudo python board-config.py …` from 28 wizard
manifest `command_list` entries. While adding a `datastore.init()` call to it,
the question came up: does the supervisor programs' `umask=002` survive down to
it?

**It does not.** The chain is:

```
supervisor [webapp] umask=002
  └─ gunicorn / app.py                              (002)
      └─ os.system("python wizard.py &")            (002)
          └─ ["sudo", python_exec, "board-config.py", ...]   ← sudo resets it
```

`sudo`'s `umask` sudoers option defaults to `0022`, and the effective value is
the **union** of the caller's umask and that setting — documented as "this
guarantees that sudo never lowers the umask when running a command". The
`/etc/sudoers.d/pifire` drop-in the installers write
(`auto-install/install-fedora.sh:274-300` and its siblings) sets no `umask`, so
the default applies. `union(002, 0022) = 0022`, which strips the group-write bit
that `auto-install/supervisor/*.conf` set `umask=002` specifically to preserve.

That matters because `control.conf`'s own comment records that control and
display run as *different users* sharing the `pifire` group, and
`pifire-install-common.sh:440` puts a setgid bit on `logs/` for the same reason.
A root-created file at `0644` is unwritable by the other user; the setgid bit
fixes the group, not the write bit.

## The survey

46 `sudo` call sites.

| Where | Count | What |
| --- | --- | --- |
| `wizard/wizard_manifest.json` | 42 | 28× `board-config.py`, 8× `raspi5.sh`, 4× `bluepy.sh`, 1× `ds18b20.sh` |
| `updater/updater_manifest.json` | 4 | `bl_udev_170.sh`, `cp …/supervisor/*.conf /etc/…`, `bluepy.sh`, `rfkill unblock bluetooth` |
| Python source | — | `apt install` (`wizard.py:448`, `updater.py:697`), `supervisorctl restart` (`common/system.py`, `display/_base_flex.py`), `reboot`/`shutdown` (`common/system.py`, `blueprints/mobile/socket_io.py`, `controller/runtime/controller.py`, `display/_base_fixed.py`), `vcgencmd` (`grillplat/raspberry_pi_all.py`), `hcitool` (`bt_diag.py`) |

Filtering to what creates files **inside the PiFire tree** — the only place the
umask can bite — leaves two candidates, and both dissolve:

- **`board-config.py`** only became one because `datastore.init()` had been added
  to it. That was removed (commit `aa69fb61`): the script only *reads* settings,
  and `read_settings()` establishes its own connection via
  `connection()`/`_ensure_schema`. It is now in the entry-point test's
  `EXCLUDED_ENTRY_POINTS` with that justification.
- **`raspi5.sh`'s `sudo tee -a /usr/local/bin/pifire/logs/wizard.log`** (lines
  3, 7, 9, 12, 14) — **refuted.** `wizard.py`'s own non-root logger creates
  `logs/wizard.log` via `create_logger`/`FileHandler` before `run_wizard()`
  executes any `command_list` entry, so the sudo `tee` always appends to a file
  that already exists with the right permissions.

Everything else writes to `/etc`, `/boot`, or nothing — root-owned by design.

## Conclusion and the open option

**There is currently no sudo call site that creates a group-write-needing file
inside the PiFire tree.** So the obvious hardening —

```
Defaults:%pifire umask=0002
```

in the sudoers drop-in — would be **preventive, not corrective**. It closes the
category before someone adds the next `sudo` helper that writes into `logs/` or
the repo root, but it fixes no live bug today.

Cost if taken: the same line has to be added to all four installer scripts
(`install.sh`, `install-debian.sh`, `install-fedora.sh`, `pifire-dietpi.sh`), and
it loosens the umask for *every* sudo command the pifire group runs, not just
PiFire's own — which is a real, if small, widening.

Deliberately not done. Revisit when a `sudo` call site is added that writes
inside the tree.

## Caveat on the evidence

sudo's `0022` default is its documented behavior, not something measured on a
target Pi. Confirm with `sudo -V | grep -i umask` on real hardware before acting
on this.
