"""
services/run_lock.py

One heavy job at a time on this machine: a detection run or a learning round (the GPU has 4 GB).
"""

import threading

_lock = threading.Lock()


def try_claim() -> bool:
    return _lock.acquire(blocking=False)


def release_claim() -> None:
    try:
        _lock.release()
    except RuntimeError:  # not held, nothing to release
        pass


def is_busy() -> bool:
    return _lock.locked()
