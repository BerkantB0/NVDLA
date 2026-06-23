from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def patch_series_fingerprint(patch_dir: Path = Path("patches/nvdla-sw")) -> dict[str, Any]:
    patches = sorted(patch_dir.glob("*.patch"))
    h = hashlib.sha256()
    names = []
    for patch in patches:
        names.append(patch.name)
        h.update(patch.name.encode("utf-8"))
        h.update(b"\0")
        h.update(patch.read_bytes())
        h.update(b"\0")
    return {
        "directory": str(patch_dir),
        "count": len(patches),
        "patches": names,
        "sha256": h.hexdigest().upper(),
    }

