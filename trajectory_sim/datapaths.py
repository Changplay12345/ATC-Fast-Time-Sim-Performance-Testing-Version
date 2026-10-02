"""Where the engine's navigation data lives.

There is one data tree — the folder the web app serves as ``/data``
(``web/public/data`` in the repo): the AIP cache, airports, airways, AIXM
procedures, sectors, FIR, holdings. It is shipped inside the program, and it
can be replaced without a new program by a **data pack**: a folder with the
same layout that the desktop shell downloads, verifies and unpacks into the
user's data directory, then names in ``$ATC_DATA_DIR`` when it starts the
engine.

Every reader of that tree gets its location from :func:`data_dir`, so
swapping the pack is one decision made in one place. A pack that is missing
or incomplete is never used: the engine falls back to the bundled data and
says so, because a simulator with yesterday's data is better than one that
does not start.

``pack.json`` at the root of the tree describes it::

    {"schema": 1, "version": "2026.09.03.1", "airac": "2026-09-03",
     "min_app": "0.3.0"}

``version`` is the AIRAC date plus a build number, compared numerically part
by part.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

PACK_FILE = "pack.json"
PACK_SCHEMA = 1
#: A file every usable data tree has; a tree without it is not a data tree.
_SENTINEL = "aip_VT.json"

_BUNDLED = Path(__file__).resolve().parents[1] / "web" / "public" / "data"


@dataclass(frozen=True)
class DataPack:
    """The data tree in use and what it says about itself."""

    path: Path
    #: "pack" when it came from ``$ATC_DATA_DIR``, "bundled" otherwise.
    source: str
    version: str | None
    airac: str | None
    #: Why ``$ATC_DATA_DIR`` was not used, when it was set but rejected.
    rejected: str | None = None


def version_key(version: str | None) -> tuple[int, ...]:
    """``"2026.09.03.1"`` -> ``(2026, 9, 3, 1)`` for comparing versions.
    Anything unparsable sorts before every real version."""
    if not version:
        return ()
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return ()


def read_pack_file(tree: Path) -> dict:
    """The tree's ``pack.json`` as a dict, or ``{}`` if absent or unreadable."""
    try:
        meta = json.loads((tree / PACK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _problem_with(tree: Path) -> str | None:
    """Why ``tree`` cannot be used as the data tree, or None if it can."""
    if not tree.is_dir():
        return f"{tree} is not a folder"
    if not (tree / _SENTINEL).is_file():
        return f"{tree} has no {_SENTINEL}"
    meta = read_pack_file(tree)
    if not meta:
        return f"{tree} has no readable {PACK_FILE}"
    if meta.get("schema") != PACK_SCHEMA:
        return f"{tree} has pack schema {meta.get('schema')!r}, this engine reads {PACK_SCHEMA}"
    if not version_key(meta.get("version")):
        return f"{tree} has no valid version in {PACK_FILE}"
    return None


def resolve(override: str | None, bundled: Path = _BUNDLED) -> DataPack:
    """Pick the data tree: ``override`` (``$ATC_DATA_DIR``) if it is usable,
    the bundled one otherwise."""
    own = read_pack_file(bundled)
    rejected = None
    if override:
        tree = Path(override)
        rejected = _problem_with(tree)
        if rejected is None:
            meta = read_pack_file(tree)
            # A program update can bring newer data than a pack downloaded
            # before it; the newer of the two wins.
            if version_key(meta.get("version")) > version_key(own.get("version")):
                return DataPack(tree, "pack", meta.get("version"), meta.get("airac"))
            rejected = (
                f"pack {meta.get('version')} is not newer than the bundled data "
                f"{own.get('version')}"
            )
        log.warning("data pack not used, falling back to the bundled data: %s", rejected)
    return DataPack(bundled, "bundled", own.get("version"), own.get("airac"), rejected)


@lru_cache(maxsize=1)
def active() -> DataPack:
    """The data tree this process uses. Decided once: the engine's worker
    processes inherit the same environment and so reach the same answer."""
    return resolve(os.environ.get("ATC_DATA_DIR"))


def data_dir() -> Path:
    """Root of the data tree in use."""
    return active().path
