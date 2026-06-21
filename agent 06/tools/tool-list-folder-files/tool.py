"""
Tool: list_folder_files
Lists all files in a folder, optionally filtered by extension.
Follows the Claude tool-use pattern: DEFINITION + run().
"""

from pathlib import Path
from datetime import datetime
from typing import List, Optional

# ── Claude tool definition ─────────────────────────────────────────────────────
DEFINITION = {
    "name": "list_folder_files",
    "description": (
        "Returns a list of all files in a specified folder. "
        "For each file it returns the filename, extension, size in bytes, "
        "and last-modified timestamp. "
        "Optionally filters results to specific file extensions. "
        "Use this at the start of any document review workflow to enumerate "
        "what files are present before deciding which ones to read or analyse."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "folder_path": {
                "type": "string",
                "description": "Absolute path to the folder to list."
            },
            "extensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of file extensions to include "
                    "(e.g. [\".pdf\", \".docx\"]). "
                    "If omitted, all files are returned."
                )
            }
        },
        "required": ["folder_path"]
    }
}


# ── Tool implementation ────────────────────────────────────────────────────────
def run(folder_path: str, extensions: Optional[List[str]] = None) -> dict:
    """
    List all files in folder_path, optionally filtered by extension.

    Returns:
        {
            "folder_path": str,
            "total_count": int,
            "files": [
                {
                    "filename": str,
                    "extension": str,
                    "size_bytes": int,
                    "last_modified": str  # ISO 8601
                },
                ...
            ]
        }
        or on error:
        {
            "error": str,
            "files": [],
            "total_count": 0
        }
    """
    folder = Path(folder_path)

    if not folder.exists() or not folder.is_dir():
        return {
            "error": f"Folder not found: {folder_path}",
            "files": [],
            "total_count": 0
        }

    # Normalise extensions to lowercase with leading dot
    filter_exts: Optional[set] = None
    if extensions:
        filter_exts = {
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in extensions
        }

    files = []
    for f in sorted(folder.rglob("*")):
        if not f.is_file():
            continue
        if filter_exts and f.suffix.lower() not in filter_exts:
            continue
        stat = f.stat()
        # Use path relative to the scanned folder so callers can reconstruct full paths
        rel = f.relative_to(folder)
        files.append({
            "filename": str(rel),
            "extension": f.suffix.lower(),
            "size_bytes": stat.st_size,
            "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        })

    return {
        "folder_path": str(folder_path),
        "files": files,
        "total_count": len(files)
    }
