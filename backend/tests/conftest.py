"""For every test in the project: no test may reach a real model by accident.

See tests/evals/gate.py. Unit, integration and golden-dataset tests all run under this
guard; only `make eval CONFIRM_REAL_MODEL=1` switches it off.
"""

from __future__ import annotations

import pytest

from tests.evals import gate


@pytest.fixture(scope="session", autouse=True)
def no_real_model_without_confirmation() -> None:
    gate.install()
