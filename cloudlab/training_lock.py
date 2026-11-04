"""One model-building process at a time, including CLI and browser starts."""
from pathlib import Path
import os


def acquire(root):
    path = Path(root)/"data"/"training.lock"
    path.parent.mkdir(parents=True,exist_ok=True)
    handle = open(path,"a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release(handle):
    if handle is None:
        return
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(handle,fcntl.LOCK_UN)
    handle.close()


def active(root):
    handle = acquire(root)
    if handle is None:
        return True
    release(handle)
    return False
