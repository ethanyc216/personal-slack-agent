from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import IO


class SingleInstanceLockError(RuntimeError):
    pass


def acquire_single_instance_lock(lock_path: Path) -> IO[str]:
    path = Path(lock_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SingleInstanceLockError(
            "Another bob-agent instance appears to be running."
        ) from exc

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle
