"""Shared read-only digest over the research corpus.

AGENTS.md rule 1 requires an immutability check before and after any run that
reads ``data/``. The digest must cover the research corpus and nothing else:
operating-system metadata files live inside ``data/`` but are not research data,
and they change whenever a Finder window touches the directory. Including them
made a legitimate build abort because ``data/.DS_Store`` was rewritten.

Excluding them narrows the guard on purpose. Every excluded name is an OS or
editor artifact that git already ignores; no corpus file matches these patterns.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

NON_CORPUS_FILENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
NON_CORPUS_PREFIXES = ("._",)
"""AppleDouble sidecars created when copying to non-HFS volumes."""


def is_corpus_file(path: Path) -> bool:
    """Whether a path under ``data/`` is research data rather than OS metadata."""
    if not path.is_file():
        return False
    if path.name in NON_CORPUS_FILENAMES:
        return False
    return not path.name.startswith(NON_CORPUS_PREFIXES)


def corpus_digest(data_directory: Path) -> str:
    """Content hash over every research file under ``data_directory``.

    Combines each file's path relative to the data root with its content hash so
    a rename is detected as a change, not just an edit.
    """
    digest = sha256()
    for path in sorted(data_directory.rglob("*")):
        if not is_corpus_file(path):
            continue
        digest.update(str(path.relative_to(data_directory)).encode("utf-8"))
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()
