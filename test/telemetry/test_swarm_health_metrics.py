"""D3: swarm-health metric helpers record the expected instruments and attributes."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("opentelemetry.sdk")

_METRICS_PROBE = """
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

reader = InMemoryMetricReader()
provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(provider)

from cli_agent_orchestrator.telemetry import (
    adjust_active_terminals,
    record_agent_step_duration,
    record_orchestration_dispatch,
    record_repo_collision,
    record_review_rejection,
    record_spawn_depth,
    record_step_attempts,
)
from cli_agent_orchestrator.telemetry import semconv

record_orchestration_dispatch("handoff")
record_agent_step_duration(
    1500,
    provider="claude_code",
    agent_profile="developer",
    model="claude-sonnet-4",
    role="developer",
    outcome="success",
)
adjust_active_terminals(1, session="cao-s1", provider="claude_code")
adjust_active_terminals(-1, session="cao-s1", provider="claude_code")
record_spawn_depth(2, orchestration_type="assign")
record_step_attempts(3, agent_profile="developer")
record_review_rejection(
    reviewer_profile="reviewer",
    reviewer_model="claude-opus-4",
    lens="code",
)
record_repo_collision(kind="multi_terminal_file")

data = reader.get_metrics_data()
assert data is not None, "no metrics exported"
names = {m.name for r in data.resource_metrics for m in r.scope_metrics for m in m.metrics}
expected = {
    "cao.orchestration.dispatches",
    "cao.agent.step.duration",
    "cao.agent.terminals.active",
    "cao.agent.spawn.depth",
    "cao.agent.step.attempts",
    "cao.review.rejections",
    "cao.repo.collisions",
}
missing = expected - names
assert not missing, f"missing instruments: {missing}; got {names}"

step_metric = next(
    m
    for rm in data.resource_metrics
    for sm in rm.scope_metrics
    for m in sm.metrics
    if m.name == "cao.agent.step.duration"
)
assert step_metric.data.data_points, "step.duration has no data points"
step_attrs = dict(step_metric.data.data_points[0].attributes)
assert step_attrs.get(semconv.CAO_MODEL) == "claude-sonnet-4", step_attrs
assert step_attrs.get(semconv.CAO_ROLE) == "developer", step_attrs
print("OK")
"""


def test_swarm_health_metrics_record_all_instruments() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _METRICS_PROBE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "OK" in proc.stdout


def test_record_agent_step_duration_requires_model_and_role() -> None:
    """Mandatory model/role are enforced by the function signature (no silent omission)."""
    import inspect

    from cli_agent_orchestrator.telemetry.metrics import record_agent_step_duration

    sig = inspect.signature(record_agent_step_duration)
    assert "model" in sig.parameters
    assert "role" in sig.parameters
    assert sig.parameters["model"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["role"].kind == inspect.Parameter.KEYWORD_ONLY
