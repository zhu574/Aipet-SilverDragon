"""Record SHA-256 checksums of staged Git content, excluding this inventory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    filename = "project-files.sha256.json"
    index = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root)
    records = []
    for entry in index.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        _mode, blob, stage = metadata.split()
        path = raw_path.decode("utf-8")
        if stage != b"0":
            raise SystemExit("Resolve staged conflicts before updating the inventory.")
        if path == filename:
            continue
        content = subprocess.check_output(["git", "cat-file", "blob", blob.decode("ascii")], cwd=root)
        records.append({"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    payload = {
        "hash_scope": "Git index blob bytes; normalized by .gitattributes; inventory excludes itself",
        "files": records,
    }
    (root / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"inventoried_files": len(records), "excluded": filename}))


if __name__ == "__main__":
    main()
