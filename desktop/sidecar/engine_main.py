"""Entry point of the packaged engine (the Tauri sidecar).

The desktop shell starts this executable with the bind address and port in
the environment, waits for /api/health, and stops it when the window closes.
It is the same FastAPI app as `uvicorn api.server:app`, just launched from
Python so PyInstaller can freeze it.

Environment:
  ATC_BIND       address to bind (default 127.0.0.1 — never 0.0.0.0 here)
  ATC_PORT       port (default 8765)
  ATC_LOG_LEVEL  uvicorn log level (default info)
  ATC_GEN_WORKERS, ATC_SESSION_TOKEN, …  read by api.server as usual
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> None:
    # MUST be the first thing in a frozen Windows executable: the batch
    # generator's ProcessPoolExecutor spawns workers by re-running this exe,
    # and this call is what makes those re-runs behave as workers instead of
    # starting another server.
    multiprocessing.freeze_support()

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
