"""The product version, read from the repo's single ``VERSION`` file.

The desktop shell, the web build and this engine all take their version from
that one file (see DESKTOP_RELEASE_PLAN.md 4.1); ``/api/health`` reports it so
the shell can refuse to run against an engine from a different release.
In the packaged engine the file sits next to the bundled packages.
"""

from __future__ import annotations

from pathlib import Path


def _read() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return "0.0.0-dev"


__version__ = _read()
