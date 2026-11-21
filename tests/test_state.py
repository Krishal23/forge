import itertools

import pytest

from forge.state import (
    GATES,
    PIPELINE,
    TERMINAL,
    TRANSITIONS,
    IllegalTransition,
    State,
    assert_transition,
    can_transition,
)


def test_happy_path_is_legal():
    for src, dst in itertools.pairwise(PIPELINE):
        assert can_transition(src, dst), f"{src} -> {dst}"


def test_merge_and_deploy_only_reachable_from_their_gates():
    for src, targets in TRANSITIONS.items():
        if State.MERGED in targets:
            assert src == State.GATE_MERGE
        if State.DEPLOYED in targets:
            assert src == State.GATE_DEPLOY


def test_cannot_skip_review_or_gates():
    for src, dst in [
        (State.IMPLEMENT, State.GATE_MERGE),
        (State.TEST, State.MERGED),
        (State.SECURITY, State.RELEASE),
        (State.RELEASE, State.MERGED),
        (State.MERGED, State.DEPLOYED),
        (State.ESCALATED, State.MERGED),
        (State.ESCALATED, State.GATE_MERGE),
    ]:
        with pytest.raises(IllegalTransition):
            assert_transition(src, dst)


def test_terminal_states_are_final():
    for s in TERMINAL:
        assert TRANSITIONS[s] == frozenset()


def test_every_work_state_can_escalate_and_abort():
    for s in State:
        if s in TERMINAL:
            continue
        assert can_transition(s, State.ABORTED)
        if s not in GATES and s != State.MERGED:
            assert can_transition(s, State.ESCALATED)


def test_failure_loops_go_back_to_implement():
    for s in (State.TEST, State.QUALITY, State.SECURITY, State.REVIEW, State.GATE_MERGE):
        assert can_transition(s, State.IMPLEMENT)
