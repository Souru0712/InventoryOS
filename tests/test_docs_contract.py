"""The product, audit and roadmap documents stay mutually consistent (scripts/check_docs.py)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_documentation_contract_holds():
    # Fixed arguments: this interpreter and the repository's own script.
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "check_docs.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
