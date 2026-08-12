"""Corpus digest guard tests.

The digest is the evidence for AGENTS.md rule 1. It must react to any research
file change and ignore operating-system metadata that lives inside ``data/``.
"""

from pathlib import Path

from defense_research_agent.data_integrity import corpus_digest, is_corpus_file


def _corpus(root: Path) -> Path:
    (root / "국방논단").mkdir(parents=True)
    (root / "국방논단" / "a.json").write_text("본문 A", encoding="utf-8")
    (root / "metadata").mkdir()
    (root / "metadata" / "b.json").write_text("본문 B", encoding="utf-8")
    return root


def test_same_tree_produces_the_same_digest(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "data")

    assert corpus_digest(root) == corpus_digest(root)


def test_content_change_is_detected(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "data")
    before = corpus_digest(root)

    (root / "metadata" / "b.json").write_text("본문 B 수정", encoding="utf-8")

    assert corpus_digest(root) != before


def test_rename_is_detected(tmp_path: Path) -> None:
    """Path is folded into the digest, so a rename is not silently equivalent."""
    root = _corpus(tmp_path / "data")
    before = corpus_digest(root)

    (root / "metadata" / "b.json").rename(root / "metadata" / "c.json")

    assert corpus_digest(root) != before


def test_new_file_is_detected(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "data")
    before = corpus_digest(root)

    (root / "metadata" / "new.json").write_text("추가", encoding="utf-8")

    assert corpus_digest(root) != before


def test_os_metadata_does_not_change_the_digest(tmp_path: Path) -> None:
    """A Finder window rewriting .DS_Store must not look like corpus tampering.

    This exact case aborted a corpus build before the exclusion existed.
    """
    root = _corpus(tmp_path / "data")
    before = corpus_digest(root)

    (root / ".DS_Store").write_bytes(b"\x00\x01finder")
    (root / "국방논단" / ".DS_Store").write_bytes(b"\x00\x02finder")
    (root / "Thumbs.db").write_bytes(b"thumbs")
    (root / "._sidecar").write_bytes(b"appledouble")

    assert corpus_digest(root) == before


def test_os_metadata_rewrite_still_does_not_change_the_digest(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "data")
    (root / ".DS_Store").write_bytes(b"first")
    before = corpus_digest(root)

    (root / ".DS_Store").write_bytes(b"second, longer content")

    assert corpus_digest(root) == before


def test_directories_are_not_hashed(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "data")
    before = corpus_digest(root)

    (root / "빈디렉터리").mkdir()

    assert corpus_digest(root) == before


def test_is_corpus_file_classification(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "data")
    (root / ".DS_Store").write_bytes(b"x")
    (root / "._sidecar").write_bytes(b"x")

    assert is_corpus_file(root / "metadata" / "b.json")
    assert not is_corpus_file(root / ".DS_Store")
    assert not is_corpus_file(root / "._sidecar")
    assert not is_corpus_file(root / "metadata")
    assert not is_corpus_file(root / "없는파일.json")


def test_empty_tree_has_a_stable_digest(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()

    assert corpus_digest(root) == corpus_digest(root)
