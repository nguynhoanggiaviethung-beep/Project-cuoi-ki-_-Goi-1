"""Process-wide lock for the standalone SSI collector worker."""
import hashlib
import os
import tempfile
from pathlib import Path


class WorkerAlreadyRunning(RuntimeError):
    pass


class CollectorWorkerLock:
    def __init__(self, identity: str):
        digest = hashlib.sha256(str(Path(identity).resolve()).encode("utf-8")).hexdigest()[:20]
        self.path = Path(tempfile.gettempdir()) / f"sli-ssi-worker-{digest}.lock"
        self._file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() == 0:
            self._file.write(b"\0")
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self._file.close()
            self._file = None
            raise WorkerAlreadyRunning("SSI collector worker đang chạy ở tiến trình khác.") from exc
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()).encode("ascii"))
        self._file.flush()
        return self

    def release(self):
        if self._file is None:
            return
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()
