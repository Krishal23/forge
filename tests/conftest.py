import pytest

from forge.config import SAMPLES_DIR, TargetConfig
from forge.tools.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    return Workspace.create("run_t", SAMPLES_DIR / "payments-service", home=tmp_path)


@pytest.fixture
def cfg(ws):
    return TargetConfig.load(ws.repo)


@pytest.fixture
def inv_ws(tmp_path):
    return Workspace.create("run_inv", SAMPLES_DIR / "inventory-service", home=tmp_path)
