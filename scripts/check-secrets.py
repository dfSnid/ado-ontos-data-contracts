"""Secret check for every file in the repository: no Databricks personal access token.

A token (dapi...) must never be committed: the pipelines sign in with workload identity
federation, so no file needs one.

Usage: python3 scripts/check-secrets.py [ROOT]   (default: the repository root)
Exit code 1 lists every hit as path:line.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CHECKS = {
    "Databricks token": re.compile(r"\bdapi[0-9a-f]{32}\b"),
}


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
                         check=True, capture_output=True, text=True).stdout
    return [root / name for name in out.splitlines() if (root / name).is_file()]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    hits = []
    for path in tracked_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # binary, for example a PNG
            continue
        for n, line in enumerate(text.splitlines(), 1):
            hits += [f"{path.relative_to(root)}:{n}: {what}" for what, rx in CHECKS.items() if rx.search(line)]
    for hit in hits:
        print(hit)
    print(f"check-secrets: {len(hits)} problems")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
