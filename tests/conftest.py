"""Test bootstrap: make the pinned Hermes checkout importable.

Puts ``.deps/hermes-agent`` on ``sys.path`` so ``agent.secret_sources.base``
resolves, and loads Hermes' conformance kit by file path — importing it as
``tests.secret_sources.conformance`` would collide with this repo's own
``tests`` package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HERMES_ROOT = REPO_ROOT / ".deps" / "hermes-agent"

if not HERMES_ROOT.is_dir():
    raise RuntimeError(
        f"Missing Hermes checkout at {HERMES_ROOT}. "
        "Run scripts/fetch-hermes.sh first."
    )

for path in (str(REPO_ROOT), str(HERMES_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


import pytest  # noqa: E402  (after sys.path bootstrap)


@pytest.fixture(autouse=True)
def isolated_bootstrap_environment(monkeypatch, tmp_path):
    """Never read real bootstrap credentials or an operator's token sink."""
    for name in ("INFISICAL_TOKEN", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.fixture(autouse=True)
def clean_project_cache():
    """Reset the process-lifetime project cache around every test."""
    from infisical_plugin.client import _reset_project_cache_for_tests

    _reset_project_cache_for_tests()
    yield
    _reset_project_cache_for_tests()


def load_hermes_conformance():
    """Load Hermes' conformance module by file path (avoids name collision)."""
    conformance_path = HERMES_ROOT / "tests" / "secret_sources" / "conformance.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_secret_source_conformance", conformance_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
