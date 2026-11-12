"""The command line loads on its own: the scheduler worker and migrations run through it."""

import os
import subprocess
import sys


def test_a_process_that_starts_from_the_command_line_loads():
    """`python -m app.cli` imports app.access before the models; an import cycle through
    app.catalog broke every command from T1.13 until T1.18, unseen by tests that load app.main."""
    result = subprocess.run(  # noqa: S603 (our own interpreter, a fixed argument list)
        [sys.executable, "-c", "import app.cli"],
        capture_output=True,
        text=True,
        env={**os.environ},
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
