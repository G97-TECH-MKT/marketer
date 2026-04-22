from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def _extract_revision_ids(text: str) -> tuple[list[str], list[str]]:
    revs = re.findall(r"Revision ID:\s*([0-9a-z_]+)", text)
    downs = re.findall(r"Revises:\s*([0-9a-z_,\s]+)", text)
    return revs, downs


def _check_downgrade(version_file: Path) -> bool:
    content = version_file.read_text(encoding="utf-8")
    return "def downgrade() -> None:" in content or "def downgrade():" in content


def main() -> int:
    versions_dir = Path("alembic/versions")
    files = sorted(versions_dir.glob("*.py"))

    if not files:
        print("No migrations found under alembic/versions")
        return 1

    missing_downgrade = [str(path) for path in files if not _check_downgrade(path)]
    if missing_downgrade:
        print("Migrations missing downgrade() implementation:")
        for item in missing_downgrade:
            print(f"- {item}")
        return 1

    history = subprocess.run(
        [sys.executable, "-m", "alembic", "history"],
        check=False,
        text=True,
        capture_output=True,
    )
    if history.returncode != 0:
        print(history.stdout)
        print(history.stderr, file=sys.stderr)
        return history.returncode

    revs, downs = _extract_revision_ids(history.stdout)
    unique_revs = set(revs)
    unique_downs = {
        rev.strip()
        for parent in downs
        for rev in parent.split(",")
        if rev.strip() and rev.strip() != "<base>"
    }

    if len(unique_revs) > 1 and len(unique_downs) == 0:
        print(
            "Detected multiple revisions with no parent references. Migration history is not linear."
        )
        return 1

    print("Alembic lineage check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
