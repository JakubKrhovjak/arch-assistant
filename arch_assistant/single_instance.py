"""One tray instance per user: an flock-ed lock file holding the pid of the running instance."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> int | None:
        """None when this process now holds the lock; otherwise the pid of the instance that holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_NB | fcntl.LOCK_EX)
        except OSError:
            fh.seek(0)
            text = fh.read().strip()
            fh.close()
            return int(text) if text.isdigit() else -1
        # the lock lives on this descriptor; rewriting the content through a second handle keeps it
        with open(self.path, "w") as writer:
            writer.write(f"{os.getpid()}\n")
        self._fh = fh
        return None

    def release(self) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
