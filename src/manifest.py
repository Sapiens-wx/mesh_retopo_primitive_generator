"""Run and per-sample metadata serialization.

Handles ``metadata.json`` (per sample), ``manifest.json`` (per run),
``run.log`` (human-readable progress), and ``errors.jsonl`` (machine-readable
failures) as described in architecture.md sections 6 and 14.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import SCHEMA_VERSION, GenerationConfig


def build_sample_metadata(
    *,
    sample_id: str,
    primitive: str,
    index: int,
    seed: int,
    generator_parameters: dict[str, Any],
    variation_parameters: dict[str, Any],
    subdivision_level: int,
    vertex_count: int,
    edge_count: int,
    face_count: int,
    bbox_dimensions: tuple[float, float, float],
    filename: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_id": sample_id,
        "primitive": primitive,
        "index": index,
        "seed": seed,
        "generator_parameters": generator_parameters,
        "variation_parameters": variation_parameters,
        "subdivision_level": subdivision_level,
        "vertex_count": vertex_count,
        "edge_count": edge_count,
        "face_count": face_count,
        "bbox_dimensions": list(bbox_dimensions),
        "filename": filename,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sample_is_complete_for_resume(sample_dir: Path, sample_id: str, filename: str) -> bool:
    """A sample counts as already-generated for ``--resume`` only when its
    FBX and metadata both exist and metadata matches this sample ID and the
    requested filename (architecture.md section 13)."""

    fbx_path = sample_dir / filename
    metadata_path = sample_dir / "metadata.json"
    if not fbx_path.is_file() or not metadata_path.is_file():
        return False
    metadata = read_json(metadata_path)
    if metadata is None:
        return False
    return metadata.get("sample_id") == sample_id and metadata.get("filename") == filename


def setup_logging(run_log_path: Path) -> logging.Logger:
    logger = logging.getLogger("primitive_dataset_generator")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(run_log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def append_error(errors_path: Path, entry: dict[str, Any]) -> None:
    with open(errors_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


@dataclass
class ManifestWriter:
    """Accumulates run-level state and writes ``manifest.json``."""

    output_root: Path
    config: GenerationConfig
    blender_version: str
    start_time_iso: str
    accepted_sample_ids: list[str] = field(default_factory=list)
    accepted_count: int = 0
    failed_count: int = 0

    def record_accepted(self, sample_id: str) -> None:
        self.accepted_sample_ids.append(sample_id)
        self.accepted_count += 1

    def record_failed(self) -> None:
        self.failed_count += 1

    def write(self, end_time_iso: str) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "blender_version": self.blender_version,
            "root_seed": self.config.seed,
            "start_time": self.start_time_iso,
            "end_time": end_time_iso,
            "cli_configuration": {
                "output": str(self.output_root),
                "samples_per_primitive": self.config.samples_per_primitive,
                "primitives": list(self.config.primitives),
                "seed": self.config.seed,
                "start_index": self.config.start_index,
                "subdivision_min": self.config.subdivision_min,
                "subdivision_max": self.config.subdivision_max,
                "max_attempts": self.config.max_attempts,
                "filename": self.config.filename,
                "overwrite": self.config.overwrite,
                "resume": self.config.resume,
                "fail_fast": self.config.fail_fast,
                "keep_failed": self.config.keep_failed,
            },
            "accepted_count": self.accepted_count,
            "failed_count": self.failed_count,
            "accepted_sample_ids": self.accepted_sample_ids,
        }
        write_json(self.output_root / "manifest.json", payload)

    @classmethod
    def load_existing_accepted_ids(cls, output_root: Path) -> list[str]:
        """Used by ``--resume`` to seed the manifest with prior accepted IDs
        (existing sample directories remain the source of truth; this is a
        convenience read for continuity of the manifest's ordered list)."""

        existing = read_json(output_root / "manifest.json")
        if not existing:
            return []
        ids = existing.get("accepted_sample_ids", [])
        return [str(i) for i in ids] if isinstance(ids, list) else []
