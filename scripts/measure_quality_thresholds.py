"""Measure corpus text quality to calibrate admission thresholds.

Read-only over ``data/``. Verifies a source-tree hash before and after so the
run provably leaves the originals untouched, per AGENTS.md rule 1. Results go to
``artifacts/quality/`` only.

Run:  uv run python scripts/measure_quality_thresholds.py
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
METADATA_DIR = DATA_DIR / "metadata"
OUT_DIR = REPO_ROOT / "artifacts" / "quality"

ALLOWED_CONTROL = {"\n", "\t", "\r"}


def source_tree_digest() -> str:
    """Content hash over every file under ``data/``, for an immutability check."""
    digest = sha256()
    for path in sorted(DATA_DIR.rglob("*")):
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(DATA_DIR)).encode("utf-8"))
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def is_control(character: str) -> bool:
    if character in ALLOWED_CONTROL:
        return False
    code = ord(character)
    return code < 0x20 or 0x7F <= code <= 0x9F


def is_korean(character: str) -> bool:
    return "가" <= character <= "힣"


def measure(text: str) -> dict[str, Any]:
    controls = Counter(character for character in text if is_control(character))
    control_count = sum(controls.values())
    korean_count = sum(1 for character in text if is_korean(character))
    length = len(text)

    # What the same document looks like if U+0001 is treated as a space first.
    normalized = text.replace("\x01", " ")
    normalized_controls = sum(1 for character in normalized if is_control(character))

    # Stricter than "not a control character": also catches format and unassigned
    # characters such as U+200B and U+00AD that survive a control-only filter.
    unprintable = sum(
        1
        for character in normalized
        if not character.isprintable() and character not in ALLOWED_CONTROL
    )

    return {
        "printable_ratio_strict": (
            (len(normalized) - unprintable) / len(normalized) if normalized else 0.0
        ),
        "character_count": length,
        "control_count": control_count,
        "control_ratio": control_count / length if length else 0.0,
        "control_codepoints": {f"U+{ord(c):04X}": n for c, n in controls.most_common(8)},
        "control_count_after_u0001_to_space": normalized_controls,
        "control_ratio_after_u0001_to_space": (
            normalized_controls / len(normalized) if normalized else 0.0
        ),
        "korean_count": korean_count,
        "korean_ratio": korean_count / length if length else 0.0,
        "printable_ratio": (length - control_count) / length if length else 0.0,
    }


def load_documents() -> list[dict[str, Any]]:
    """One record per unique document, keyed by full-text checksum."""
    by_checksum: dict[str, dict[str, Any]] = {}
    for path in sorted(METADATA_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        text = payload.get("full_text") or ""
        meta = payload.get("metadata") or {}
        checksum = sha256(text.encode("utf-8")).hexdigest()
        if checksum in by_checksum:
            by_checksum[checksum]["duplicate_files"].append(path.name)
            continue
        pages = payload.get("page_texts") or []
        non_empty = sum(1 for page in pages if str(page.get("text", "")).strip())
        by_checksum[checksum] = {
            "file": path.name,
            "duplicate_files": [],
            "category": meta.get("category", "unknown"),
            "page_count": len(pages),
            "non_empty_page_count": non_empty,
            "non_empty_page_ratio": non_empty / len(pages) if pages else 0.0,
            **measure(text),
        }
    return list(by_checksum.values())


def sweep(
    documents: list[dict[str, Any]], key: str, thresholds: list[float], *, below: bool
) -> list[dict[str, Any]]:
    """How many documents each candidate threshold would exclude."""
    rows = []
    total = len(documents)
    for threshold in thresholds:
        if below:
            hit = [d for d in documents if d[key] < threshold]
        else:
            hit = [d for d in documents if d[key] > threshold]
        by_category = Counter(d["category"] for d in hit)
        rows.append(
            {
                "threshold": threshold,
                "excluded": len(hit),
                "excluded_pct": round(100 * len(hit) / total, 1) if total else 0.0,
                "by_category": dict(sorted(by_category.items())),
            }
        )
    return rows


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))
        return round(ordered[index], 6)

    return {
        "min": at(0.0),
        "p10": at(0.10),
        "p50": at(0.50),
        "p90": at(0.90),
        "p99": at(0.99),
        "max": at(1.0),
    }


def main() -> None:
    before = source_tree_digest()
    documents = load_documents()

    control_codepoints: Counter[str] = Counter()
    for document in documents:
        for codepoint, count in document["control_codepoints"].items():
            control_codepoints[codepoint] += count

    report: dict[str, Any] = {
        "unique_documents": len(documents),
        "metadata_files": len(list(METADATA_DIR.glob("*.json"))),
        "documents_with_control_characters": sum(1 for d in documents if d["control_count"] > 0),
        "documents_with_control_characters_by_category": dict(
            sorted(Counter(d["category"] for d in documents if d["control_count"] > 0).items())
        ),
        "documents_by_category": dict(sorted(Counter(d["category"] for d in documents).items())),
        "top_control_codepoints": {
            codepoint: {
                "count": count,
                "name": unicodedata.name(chr(int(codepoint[2:], 16)), "<unnamed>"),
            }
            for codepoint, count in control_codepoints.most_common(10)
        },
        "control_ratio_percentiles": percentiles([d["control_ratio"] for d in documents]),
        "control_ratio_percentiles_after_u0001_to_space": percentiles(
            [d["control_ratio_after_u0001_to_space"] for d in documents]
        ),
        "korean_ratio_percentiles": percentiles([d["korean_ratio"] for d in documents]),
        "printable_ratio_strict_percentiles": percentiles(
            [d["printable_ratio_strict"] for d in documents]
        ),
        "non_empty_page_ratio_percentiles": percentiles(
            [d["non_empty_page_ratio"] for d in documents]
        ),
        "character_count_percentiles": percentiles(
            [float(d["character_count"]) for d in documents]
        ),
        "sweeps": {
            "max_control_character_ratio": sweep(
                documents,
                "control_ratio",
                [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3],
                below=False,
            ),
            "max_control_character_ratio_after_u0001_to_space": sweep(
                documents,
                "control_ratio_after_u0001_to_space",
                [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3],
                below=False,
            ),
            "min_korean_ratio": sweep(
                documents, "korean_ratio", [0.01, 0.05, 0.1, 0.2, 0.3, 0.4], below=True
            ),
            "min_printable_ratio_strict": sweep(
                documents,
                "printable_ratio_strict",
                [0.9, 0.95, 0.99, 0.999, 0.9999],
                below=True,
            ),
            "min_non_empty_page_ratio": sweep(
                documents,
                "non_empty_page_ratio",
                [0.1, 0.25, 0.5, 0.75, 0.9],
                below=True,
            ),
            "min_character_count": sweep(
                documents,
                "character_count",
                [100, 500, 1_000, 2_000, 5_000],
                below=True,
            ),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "threshold_calibration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "per_document.json").write_text(
        json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    after = source_tree_digest()
    if before != after:
        raise SystemExit(f"data/ changed during the run: {before} -> {after}")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\ndata/ immutable: {before[:16]}… unchanged")


if __name__ == "__main__":
    main()
