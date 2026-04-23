from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


CANONICAL_MODE_CHOICES = ('check', 'update-baseline', 'full')
MODE_CHOICES = CANONICAL_MODE_CHOICES


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


@dataclass(slots=True)
class RenameSample:
    sample_id: str
    sample_json: str
    check: bool = False
    anchor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(slots=True)
class ManifestSnapshot:
    manifest_version: str
    mode: str
    selected_count: int
    selected_sample_ids: list[str]
    samples: list[dict[str, Any]]
    selection_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(slots=True)
class RunContext:
    run_id: str
    mode: str
    started_at: str
    manifest_version: str
    manifest_snapshot_path: str
    baseline_root: str
    artifacts_root: str
    ai_model_info: dict[str, Any] = field(default_factory=dict)
    provider_version_info: dict[str, Any] = field(default_factory=dict)
    selected_sample_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(slots=True)
class SampleRunResult:
    sample_id: str
    status: str
    anchor: bool
    is_flaky: bool
    infra_failure: bool
    retry_count: int
    comparison_summary: dict[str, Any]
    artifacts: dict[str, Any]
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(slots=True)
class RunSummary:
    selected_count: int
    completed_count: int
    passed_count: int
    product_failure_count: int
    infra_failure_count: int
    flaky_count: int
    baseline_missing_count: int
    manual_review_count: int
    sample_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(slots=True)
class RunReport:
    run_context: dict[str, Any]
    summary: dict[str, Any]
    gate_result: dict[str, Any]
    flaky_samples: list[str] = field(default_factory=list)
    infra_failures: list[str] = field(default_factory=list)
    observation_failures: list[str] = field(default_factory=list)
    quarantine_candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(slots=True)
class BaselineRecord:
    sample_id: str
    schema_version: int
    anchor: bool
    captured_at: str
    runtime_signature: dict[str, Any]
    expected: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))
