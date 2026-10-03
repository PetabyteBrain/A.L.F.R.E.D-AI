"""Behaviour checks against the real model (orchestrator/evals.py). Costs a little, so opt-in:

    RUN_EVALS=1 ANTHROPIC_API_KEY=sk-... pytest tests/test_evals.py

Runs in its own process because the checks use their own throwaway database and settings.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.environ.get("RUN_EVALS") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
                    reason="behaviour checks call the real model; set RUN_EVALS=1 and ANTHROPIC_API_KEY")
def test_behaviour_checks():
    out = subprocess.run([sys.executable, "-m", "orchestrator.evals"], cwd=Path(__file__).resolve().parent.parent,
                         capture_output=True, text=True, timeout=900)
    print(out.stdout)
    assert out.returncode == 0, out.stdout + out.stderr
