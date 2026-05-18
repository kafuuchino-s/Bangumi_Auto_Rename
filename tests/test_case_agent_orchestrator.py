from __future__ import annotations

import pytest


pytest.skip(
    'legacy Python state-machine orchestrator tests removed from active suite; '
    'Local->Bangumi now uses the OrchestratorAgent tool loop',
    allow_module_level=True,
)
