"""Subprocess tests for NEURO_REQUIRE_PUBMED_EMAIL enforcement.

Why subprocess? `settings.py` reads the env vars at module import time. To
exercise all three modes (default warn, enforced+placeholder, enforced+real)
we need fresh Python processes with controlled environments — `monkeypatch`
and `importlib.reload()` are fragile because submodules (`clients.pubmed`)
capture `settings.PUBMED_API_KEY` at *their* import time as well.

Subprocess gives us guaranteed isolation per case.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def _run_settings(env_overrides: dict[str, str], remove: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run a fresh python that imports settings, in a clean env.

    Returns a CompletedProcess with exit code and stderr (which contains the
    warning, if any).
    """
    env = os.environ.copy()
    for key in remove or []:
        env.pop(key, None)
    env.update(env_overrides)
    # `python -c "import neuro_research_discovery.settings"` is the smallest
    # thing that triggers the enforcement path.
    return subprocess.run(
        [sys.executable, "-c", "import neuro_research_discovery.settings as s; print('loaded')"],
        capture_output=True,
        text=True,
        env=env,
    )


def test_default_mode_warns_but_loads():
    """No enforcement, placeholder email: server starts, warning on stderr."""
    proc = _run_settings(
        env_overrides={},
        remove=["NEURO_REQUIRE_PUBMED_EMAIL", "PUBMED_EMAIL"],
    )
    assert proc.returncode == 0, f"settings should load; stderr={proc.stderr}"
    assert "loaded" in proc.stdout
    assert "PUBMED_EMAIL is not set" in proc.stderr


def test_enforcement_with_placeholder_refuses_to_start():
    """NEURO_REQUIRE_PUBMED_EMAIL=1 + placeholder email: raise at import."""
    proc = _run_settings(
        env_overrides={"NEURO_REQUIRE_PUBMED_EMAIL": "1"},
        remove=["PUBMED_EMAIL"],
    )
    assert proc.returncode != 0, "settings should refuse to import"
    assert "RuntimeError" in proc.stderr
    assert "NEURO_REQUIRE_PUBMED_EMAIL=1" in proc.stderr


def test_enforcement_with_real_email_loads_cleanly():
    """NEURO_REQUIRE_PUBMED_EMAIL=1 + real email: server starts without warning."""
    proc = _run_settings(
        env_overrides={
            "NEURO_REQUIRE_PUBMED_EMAIL": "1",
            "PUBMED_EMAIL": "real.researcher@institution.tld",
        },
    )
    assert proc.returncode == 0, f"settings should load; stderr={proc.stderr}"
    assert "loaded" in proc.stdout
    # No warning when the real email is set.
    assert "PUBMED_EMAIL is not set" not in proc.stderr


@pytest.mark.parametrize("truthy_value", ["1", "true", "TRUE", "yes", "YES"])
def test_enforcement_accepts_common_truthy_values(truthy_value: str):
    """Any of {1, true, yes} (case-insensitive) should enable enforcement."""
    proc = _run_settings(
        env_overrides={"NEURO_REQUIRE_PUBMED_EMAIL": truthy_value},
        remove=["PUBMED_EMAIL"],
    )
    assert proc.returncode != 0, (
        f"value {truthy_value!r} should enable enforcement; "
        f"stderr={proc.stderr}"
    )


@pytest.mark.parametrize("falsy_value", ["", "0", "false", "no", "off"])
def test_enforcement_ignores_falsy_values(falsy_value: str):
    """Empty or {0, false, no, off} should leave enforcement off."""
    proc = _run_settings(
        env_overrides={"NEURO_REQUIRE_PUBMED_EMAIL": falsy_value},
        remove=["PUBMED_EMAIL"],
    )
    assert proc.returncode == 0, (
        f"value {falsy_value!r} should NOT enable enforcement; "
        f"stderr={proc.stderr}"
    )
