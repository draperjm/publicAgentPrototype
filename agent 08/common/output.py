"""
Standardised output file management.

Every agent writes artefacts to a timestamped JSON file and returns
the path + metadata in a FilesManifest. This module centralises that
pattern so agents don't each implement their own directory/naming logic.

Usage:
    from common.output import OutputManager

    out = OutputManager(job_dir=request.output_dir)

    path = out.write(payload, prefix="DocumentReview", role="document_review",
                     description="Document classification results")

    return {**payload, "output_file": str(path), "files": out.manifest().to_dict()}
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from common.config import settings
from common.models import FileEntry, FilesManifest


class OutputManager:
    """
    Manages output file creation for a single agent invocation.

    Parameters
    ----------
    base_dir:
        Root output directory (defaults to settings.output_dir).
    job_dir:
        Specific subdirectory for this job/execution. If None the
        base_dir is used directly.
    """

    def __init__(
        self,
        base_dir: Optional[str] = None,
        job_dir: Optional[str] = None,
    ):
        root = Path(base_dir) if base_dir else Path(settings.output_dir)
        self._dir = Path(job_dir) if job_dir else root
        self._ensure_dir(self._dir)
        self._manifest = FilesManifest()

    # ── Public API ──────────────────────────────────────────────────────────────

    @property
    def directory(self) -> Path:
        return self._dir

    def write(
        self,
        data: Any,
        prefix: str,
        role: str = "output",
        description: str = "",
        timestamp: Optional[str] = None,
    ) -> Path:
        """
        Serialise `data` to a timestamped JSON file and register it in
        the manifest.  Returns the full path of the written file.
        """
        ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{ts}.json"
        path = self._dir / filename
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        self._manifest.add_output(
            filename=filename,
            path=str(path),
            role=role,
            description=description,
        )
        return path

    def register_read(
        self,
        filename: str,
        path: str,
        description: str = "",
        **kwargs,
    ) -> None:
        """Record a file that was read as input (for provenance tracking)."""
        self._manifest.add_read(filename=filename, path=path, description=description, **kwargs)

    def manifest(self) -> FilesManifest:
        """Return the accumulated FilesManifest for this invocation."""
        return self._manifest

    def read_json(self, path: str | Path) -> Any:
        """
        Read a JSON file from disk and register it as a read artefact.
        Convenience method so agents don't need separate open() calls.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"OutputManager.read_json: file not found: {path}")
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.register_read(filename=p.name, path=str(p))
        return data

    # ── Helpers ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _ensure_dir(path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            fallback = Path(settings.output_dir)
            fallback.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def timestamp() -> str:
        """Return the current timestamp string used for file naming."""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def iso_timestamp() -> str:
        """Return ISO 8601 timestamp suitable for report metadata."""
        return datetime.now().isoformat()
