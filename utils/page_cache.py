"""Drop a file from the kernel page cache once we are done with it.

Railway counts a container's page cache as service memory (and bills it). Our
daily backup reads every stored file and writes a multi-GB archive, which used
to leave the service at ~3.4 GB "in use" until the next restart. Telling the
kernel we will not need those pages again keeps the reported memory near the
processes' real usage.
"""

import os


def drop_page_cache(path: str, *, flush: bool = False) -> None:
    """Best effort; never raises. ``flush=True`` writes dirty pages first
    (needed for a file we just wrote, since only clean pages can be dropped)."""
    if not hasattr(os, "posix_fadvise"):
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        if flush:
            os.fdatasync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass
    finally:
        os.close(fd)
