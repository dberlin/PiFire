"""Repair service-created venv directories before unprivileged uv mutations.

Only directories are changed: package files may be hard-linked into uv's cache.
This entrypoint uses only the standard library so old updaters can run it before
synchronizing the environment from the newly checked-out tree.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path


def _prepare_directories(environment: Path, uid: int, gid: int) -> None:
    # Anchor traversal to an opened directory and never follow package symlinks.
    descriptor = os.open(environment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:

        def raise_walk_error(error: OSError) -> None:
            raise error

        for _path, _directories, _files, directory in os.fwalk(
            ".", dir_fd=descriptor, follow_symlinks=False, onerror=raise_walk_error
        ):
            metadata = os.fstat(directory)
            ownership_wrong = metadata.st_uid != uid or metadata.st_gid != gid
            inaccessible = stat.S_IMODE(metadata.st_mode) & 0o700 != 0o700
            if ownership_wrong or inaccessible:
                if ownership_wrong:
                    os.fchown(directory, uid, gid)
                os.fchmod(directory, stat.S_IMODE(metadata.st_mode) | 0o700)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    repository = parser.parse_args(argv).repo.resolve()
    environment = repository / ".venv"
    try:
        if environment.is_symlink():
            raise ValueError(f"Refusing symbolic link virtual environment: {environment}")
        if not environment.exists():
            return 0
        owner = repository.stat()
        privileged = os.geteuid() == 0
        try:
            _prepare_directories(environment, owner.st_uid, owner.st_gid)
        except PermissionError:
            if privileged:
                raise
        else:
            return 0
        print(f"Repairing virtual environment directory ownership for uid={owner.st_uid}: {environment}", flush=True)
        return subprocess.run(
            [
                "sudo",
                "-n",
                # Shipped sudoers authorizes bash, not the system Python used
                # by an old updater's pre-sync migration. Pass argv unchanged.
                "bash",
                "-c",
                'exec "$@"',
                "pifire-venv-repair",
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                "--repo",
                str(repository),
            ],
            check=False,
        ).returncode
    except (OSError, ValueError) as error:
        print(f"Cannot prepare Python environment: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
