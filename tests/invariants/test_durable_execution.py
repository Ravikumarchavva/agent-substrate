"""Invariant register — durable execution (rows I7-I13, I15).

Each test is one guarantee, asserted over *every* durable write of a scenario at
once, in both failure modes (a blip the worker handles, and a worker killed outright
whose lease has to expire). A failure names every point that violates it rather than
the first.

What "correct" means after a failure, for a tool that is not safe to run twice:

* it never runs twice (I9) — a crash can lose the *record* of a run, never cause a
  second charge;
* the run still ends (I10) — completed if the failure left nothing in doubt, failed
  if it did, but never stranded;
* the run's record has one terminal entry (I11).
"""

from __future__ import annotations

import asyncio

import pytest

from tests.invariants._harness.crash import Mode, Observation, crash_matrix, report
from tests.invariants._harness.scenarios import one_tool_call, spawn_and_join

MODES: list[Mode] = ["blip", "death"]


@pytest.fixture(scope="module", params=MODES)
def tool_matrix(request: pytest.FixtureRequest) -> list[Observation]:
    return asyncio.run(crash_matrix(one_tool_call, mode=request.param))


@pytest.fixture(scope="module", params=MODES)
def spawn_matrix(request: pytest.FixtureRequest) -> list[Observation]:
    return asyncio.run(crash_matrix(spawn_and_join, mode=request.param))


def _crashed(matrix: list[Observation]) -> list[Observation]:
    """The crash points that happen after submission. Point 0 is the submit itself:
    the caller was told it failed, so there is legitimately no run."""
    return [o for o in matrix if o.index not in (None, 0)]


def test_the_clean_run_is_correct(tool_matrix: list[Observation]) -> None:
    """The baseline: with no failure the card is charged once and the run completes."""
    clean = tool_matrix[0]
    assert clean.terminal == "run.completed", report(tool_matrix)
    assert clean.side_effects == [100], report(tool_matrix)
    assert clean.terminal_entries == ["run.completed"], report(tool_matrix)


def test_i07_intent_is_journaled_before_execution(
    tool_matrix: list[Observation],
) -> None:
    """A journaled call records its intent before it runs, so a replay can tell
    'never started' from 'started, outcome unknown'."""
    clean = tool_matrix[0]
    assert "effect.intent" in clean.log_kinds, report(tool_matrix)
    assert clean.log_kinds.index("effect.intent") < clean.log_kinds.index(
        "tool.call"
    ), f"the intent was not recorded before the tool ran: {clean.log_kinds}"


def test_i09_a_completed_effect_never_re_executes(
    tool_matrix: list[Observation],
) -> None:
    """However the runtime fails and recovers, a tool that is not safe to run twice
    never runs twice."""
    offenders = [o for o in _crashed(tool_matrix) if len(o.side_effects) > 1]
    assert not offenders, (
        f"the card was charged more than once:\n{report(offenders)}\n\nfull matrix:\n{report(tool_matrix)}"
    )


def test_i10_a_single_durable_failure_still_reaches_a_terminal_state(
    tool_matrix: list[Observation],
) -> None:
    """One failed write must not strand a run. A run that never ends holds its thread's
    single-flight slot forever and never reports to its caller."""
    stranded = [o for o in _crashed(tool_matrix) if o.terminal is None]
    assert not stranded, (
        f"runs stranded with no terminal entry:\n{report(stranded)}\n\nfull matrix:\n{report(tool_matrix)}"
    )


def test_i11_at_most_one_terminal_entry_per_run(tool_matrix: list[Observation]) -> None:
    """A run's record is the source of truth for history, billing and projection. Two
    terminal entries make all three wrong."""
    offenders = [o for o in _crashed(tool_matrix) if len(o.terminal_entries) != 1]
    assert not offenders, (
        f"runs whose record does not have exactly one terminal entry:\n{report(offenders)}"
    )


def test_a_failure_the_worker_can_handle_never_loses_the_run(
    tool_matrix: list[Observation],
) -> None:
    """A dropped connection, not a crash: the work was done, only a record failed, so
    the run completes — retrying the record rather than failing the run."""
    if tool_matrix[0].mode != "blip":
        pytest.skip("applies to failures the worker survives")
    failed = [o for o in _crashed(tool_matrix) if o.terminal != "run.completed"]
    assert not failed, f"a transient store failure failed the run:\n{report(failed)}"


def test_i08_a_killed_worker_leaves_an_effect_in_doubt_and_the_run_says_so(
    tool_matrix: list[Observation],
) -> None:
    """If the worker dies after starting a tool that is not safe to run twice, nothing
    can say whether it took effect. The run fails rather than guess; the journaled
    intent is what a person compensates from."""
    if tool_matrix[0].mode != "death":
        pytest.skip("applies to a killed worker")
    in_doubt = [o for o in _crashed(tool_matrix) if o.terminal == "run.failed"]
    assert in_doubt, (
        "no crash point left an effect in doubt — the scenario is not exercising the window"
    )
    for o in in_doubt:
        assert len(o.side_effects) <= 1, str(o)


def test_i09_spawned_children_run_their_journaled_effects_once(
    spawn_matrix: list[Observation],
) -> None:
    """A child's side effects are as protected as a parent's, and the parent is woken
    exactly once."""
    offenders = [o for o in _crashed(spawn_matrix) if len(o.side_effects) > 1]
    assert not offenders, f"the child's effect ran more than once:\n{report(offenders)}"


def test_i11_a_parent_run_ends_exactly_once_even_when_its_child_does(
    spawn_matrix: list[Observation],
) -> None:
    offenders = [o for o in _crashed(spawn_matrix) if len(o.terminal_entries) != 1]
    assert not offenders, f"{report(offenders)}\n\nfull matrix:\n{report(spawn_matrix)}"


def test_i10_a_parent_waiting_on_a_child_is_never_stranded(
    spawn_matrix: list[Observation],
) -> None:
    stranded = [o for o in _crashed(spawn_matrix) if o.terminal is None]
    assert not stranded, f"{report(stranded)}\n\nfull matrix:\n{report(spawn_matrix)}"


def test_the_matrix_actually_injected_every_point(
    tool_matrix: list[Observation],
) -> None:
    """Guards the harness itself: a matrix that silently stopped injecting would make
    every row above pass for the wrong reason."""
    crashed = _crashed(tool_matrix)
    assert crashed, "the matrix produced no crash points"
    missed = [o for o in crashed if o.crashed_at is None]
    assert not missed, f"crash points that never injected:\n{report(missed)}"
