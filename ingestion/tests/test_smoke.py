"""Live integration tests — thin pytest wrapper around scripts/smoke.py."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.smoke,
    pytest.mark.skipif(not os.getenv("ZEP_API_KEY"), reason="ZEP_API_KEY not set"),
]


def test_smoke_suite():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "smoke.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=root,
        check=False,
    )
    assert completed.returncode == 0, "smoke suite failed; see output above"
