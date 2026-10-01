"""Entry point of the packaged engine (the desktop shell's sidecar).

The shell starts this executable with the bind address, port and session
token in the environment, waits for /api/health, and stops it when the window
closes. It is the same FastAPI app as `uvicorn api.server:app`, launched from
Python so PyInstaller can freeze it.

Environment:
  ATC_BIND          address to bind (default 127.0.0.1 — never 0.0.0.0 here)
  ATC_PORT          port (default 8765)
  ATC_PARENT_PID    the shell's process id; the engine exits when it is gone
  ATC_LOG_LEVEL     uvicorn log level (default info)
  ATC_LOCAL_MODE, ATC_SESSION_TOKEN, ATC_OUT_DIR, ATC_GEN_WORKERS, WEB_ORIGIN
                    read by api.server
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import time


def _exit_when_parent_dies(pid: int) -> None:
    """Stop this engine if the shell that started it disappears.

    The shell stops the engine itself on a normal exit. This covers the rest —
    a crash, a forced kill, an installer closing the app to update it — where
    an engine left running would hold its memory, and on Windows keep its own
    files locked so the update could not replace them.
    """

    def watch() -> None:
        if sys.platform == "win32":
            import ctypes

            SYNCHRONIZE = 0x0010_0000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if not handle:
                return  # already gone, or not ours to watch — nothing to wait on
            kernel32.WaitForSingleObject(handle, 0xFFFF_FFFF)
        else:
            while os.getppid() == pid:
                time.sleep(1.0)
        # Hard exit: the worker processes watch THIS process the same way.
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def main() -> None:
    # MUST be the first thing in a frozen Windows executable: the batch
    # generator's ProcessPoolExecutor spawns workers by re-running this exe,
    # and this call is what makes those re-runs behave as workers instead of
    # starting another server.
    multiprocessing.freeze_support()

    parent = os.environ.get("ATC_PARENT_PID", "").strip()
    if parent.isdigit():
        _exit_when_parent_dies(int(parent))

    host = os.environ.get("ATC_BIND", "127.0.0.1")
    port = int(os.environ.get("ATC_PORT", "8765"))
    log_level = os.environ.get("ATC_LOG_LEVEL", "info")

    # Imported here, after freeze_support(), so a worker process never pays
    # for (or re-runs) the server import.
    import uvicorn

    from api.server import app

    print(f"[engine] starting on http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
