"""GenAI-style metric instruments for CAO orchestration (opt-in).

Like the span helpers, these are inert unless the ``[otel]`` extra is installed
and the SDK is activated. The meter and its instruments are resolved lazily at
call time so they bind to the ``MeterProvider`` installed by ``init_telemetry``
in the app lifespan — never a pre-init no-op provider (same lesson as the span
helpers).
"""

from __future__ import annotations

from typing import Optional

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, UpDownCounter

from cli_agent_orchestrator.telemetry import semconv

_METER_NAME = "cli_agent_orchestrator"
_dispatch_counter: Optional[Counter] = None
_step_duration_histogram: Optional[Histogram] = None
_active_terminals_counter: Optional[UpDownCounter] = None
_spawn_depth_histogram: Optional[Histogram] = None
_step_attempts_histogram: Optional[Histogram] = None
_review_rejections_counter: Optional[Counter] = None
_repo_collisions_counter: Optional[Counter] = None


def _dispatch_counter_instrument() -> Counter:
    """Lazily create (once) the orchestration-dispatch counter."""

    global _dispatch_counter
    if _dispatch_counter is None:
        _dispatch_counter = metrics.get_meter(_METER_NAME).create_counter(
            "cao.orchestration.dispatches",
            unit="1",
            description=(
                "Count of inter-agent orchestration dispatches "
                "(send_message / handoff / assign), by orchestration type."
            ),
        )
    return _dispatch_counter


def _step_duration_histogram_instrument() -> Histogram:
    """D3: lazily create the agent step duration histogram (milliseconds)."""

    global _step_duration_histogram
    if _step_duration_histogram is None:
        _step_duration_histogram = metrics.get_meter(_METER_NAME).create_histogram(
            "cao.agent.step.duration",
            unit="ms",
            description=(
                "Wall-clock duration of an agent step (handoff, workflow step, etc.), "
                "by provider, profile, model, role, and outcome."
            ),
        )
    return _step_duration_histogram


def _active_terminals_counter_instrument() -> UpDownCounter:
    """D3: lazily create the active-terminals up-down counter."""

    global _active_terminals_counter
    if _active_terminals_counter is None:
        _active_terminals_counter = metrics.get_meter(_METER_NAME).create_up_down_counter(
            "cao.agent.terminals.active",
            unit="1",
            description="Number of concurrently active agent terminals, by session and provider.",
        )
    return _active_terminals_counter


def _spawn_depth_histogram_instrument() -> Histogram:
    """D3: lazily create the spawn-depth histogram."""

    global _spawn_depth_histogram
    if _spawn_depth_histogram is None:
        _spawn_depth_histogram = metrics.get_meter(_METER_NAME).create_histogram(
            "cao.agent.spawn.depth",
            unit="1",
            description="Spawn depth at orchestration dispatch time, by orchestration type.",
        )
    return _spawn_depth_histogram


def _step_attempts_histogram_instrument() -> Histogram:
    """D3: lazily create the step-attempts histogram."""

    global _step_attempts_histogram
    if _step_attempts_histogram is None:
        _step_attempts_histogram = metrics.get_meter(_METER_NAME).create_histogram(
            "cao.agent.step.attempts",
            unit="1",
            description="Number of attempts per agent step, by agent profile.",
        )
    return _step_attempts_histogram


def _review_rejections_counter_instrument() -> Counter:
    """D3: lazily create the review-rejections counter."""

    global _review_rejections_counter
    if _review_rejections_counter is None:
        _review_rejections_counter = metrics.get_meter(_METER_NAME).create_counter(
            "cao.review.rejections",
            unit="1",
            description="Count of review rejections, by reviewer profile, model, and lens.",
        )
    return _review_rejections_counter


def _repo_collisions_counter_instrument() -> Counter:
    """D3: lazily create the repo-collisions counter (detection wired in D5)."""

    global _repo_collisions_counter
    if _repo_collisions_counter is None:
        _repo_collisions_counter = metrics.get_meter(_METER_NAME).create_counter(
            "cao.repo.collisions",
            unit="1",
            description=(
                "Count of repo collision events (multi-terminal file overlap, clobbers). "
                "Minimal attribute set until D5 collision detection lands."
            ),
        )
    return _repo_collisions_counter


def record_orchestration_dispatch(orchestration_type: str) -> None:
    """Increment the orchestration-dispatch counter (no-op when telemetry off)."""

    _dispatch_counter_instrument().add(1, {semconv.CAO_ORCHESTRATION_TYPE: orchestration_type})


def record_agent_step_duration(
    duration_ms: int,
    *,
    provider: str,
    agent_profile: str,
    model: str,
    role: str,
    outcome: str,
) -> None:
    """D3: record cao.agent.step.duration (no-op when telemetry off).

    ``model`` and ``role`` are mandatory — T2 cost slicing depends on them.
    """

    _step_duration_histogram_instrument().record(
        duration_ms,
        {
            semconv.CAO_PROVIDER: provider,
            semconv.CAO_AGENT_PROFILE: agent_profile,
            semconv.CAO_MODEL: model,
            semconv.CAO_ROLE: role,
            semconv.CAO_OUTCOME: outcome,
        },
    )


def adjust_active_terminals(delta: int, *, session: str, provider: str) -> None:
    """D3: adjust cao.agent.terminals.active by ``delta`` (no-op when telemetry off)."""

    _active_terminals_counter_instrument().add(
        delta,
        {
            semconv.CAO_SESSION: session,
            semconv.CAO_PROVIDER: provider,
        },
    )


def record_spawn_depth(depth: int, *, orchestration_type: str) -> None:
    """D3: record cao.agent.spawn.depth (no-op when telemetry off)."""

    _spawn_depth_histogram_instrument().record(
        depth,
        {semconv.CAO_ORCHESTRATION_TYPE: orchestration_type},
    )


def record_step_attempts(attempts: int, *, agent_profile: str) -> None:
    """D3: record cao.agent.step.attempts (no-op when telemetry off)."""

    _step_attempts_histogram_instrument().record(
        attempts,
        {semconv.CAO_AGENT_PROFILE: agent_profile},
    )


def record_review_rejection(
    *,
    reviewer_profile: str,
    reviewer_model: str,
    lens: str,
) -> None:
    """D3: increment cao.review.rejections (no-op when telemetry off)."""

    _review_rejections_counter_instrument().add(
        1,
        {
            semconv.CAO_REVIEWER_PROFILE: reviewer_profile,
            semconv.CAO_REVIEWER_MODEL: reviewer_model,
            semconv.CAO_LENS: lens,
        },
    )


def record_repo_collision(*, kind: str) -> None:
    """D3: increment cao.repo.collisions (no-op when telemetry off).

    ``kind`` is the minimal attribute until D5 defines the full collision taxonomy.
    """

    _repo_collisions_counter_instrument().add(1, {semconv.CAO_COLLISION_KIND: kind})
