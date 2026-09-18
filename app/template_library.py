"""Read-only listing of the operator-supplied template directory.

The directory is whatever the operator points ``TEMPLATE_DIR`` at -- in the
container it is ``/templates``, which is meant to be bind-mounted from the
host. DynaForm only ever *reads* from it: choosing a template copies its text
into the textarea on the first page, and whatever the user edits there belongs
to that request alone. The file on disk is never written to.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from flask import current_app

SUFFIXES = frozenset({".j2", ".txt"})


@dataclass(frozen=True)
class LibraryTemplate:
    name: str  # path relative to the root; the <option> value and lookup key
    label: str  # what the <select> shows
    path: Path  # absolute, already confirmed to live under the root


def library_root() -> Path | None:
    """The configured directory, or None when unset or unusable."""
    configured = (current_app.config.get("TEMPLATE_DIR") or "").strip()
    if not configured:
        return None
    try:
        root = Path(configured).resolve(strict=True)
    except OSError:
        return None
    return root if root.is_dir() else None


def list_templates() -> list[LibraryTemplate]:
    """Scan the template directory for readable .j2/.txt files.

    Scanned per request rather than cached, so a file dropped into the
    bind-mounted directory shows up on the next page load with no restart.
    """
    root = library_root()
    if root is None:
        return []

    max_bytes = current_app.config["TEMPLATE_MAX_BYTES"]
    found: list[LibraryTemplate] = []

    # followlinks=False: a symlinked *directory* is never descended into, so a
    # link aimed at / can't turn this into a walk of the whole filesystem.
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            if filename.startswith(".") or Path(filename).suffix.lower() not in SUFFIXES:
                continue
            path = Path(dirpath, filename)
            try:
                # resolve() follows a symlinked *file*; the containment check
                # below then rejects one aimed outside the directory.
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    continue
                if resolved.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            name = path.relative_to(root).as_posix()
            found.append(LibraryTemplate(name=name, label=name, path=resolved))

    found.sort(key=lambda template: template.name)
    return found


def read_template(name: str) -> str | None:
    """Return a listed template's text, or None when it isn't listed.

    The name is matched against the scan rather than joined onto the root, so
    a crafted value ("../../etc/passwd") has nothing to match and is simply
    not found -- there is no user input on the path-building side at all.
    """
    for template in list_templates():
        if template.name == name:
            try:
                return template.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
    return None
