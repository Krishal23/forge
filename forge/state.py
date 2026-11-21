"""Workflow states and the transitions allowed between them.

MERGED and DEPLOYED can only be reached from their human gates.
"""

from __future__ import annotations

from enum import Enum


class State(str, Enum):
    INTAKE = "INTAKE"
    ANALYSIS = "ANALYSIS"
    GATE_CRITERIA = "GATE_CRITERIA"  # human confirms acceptance criteria / answers questions
    DESIGN = "DESIGN"
    IMPLEMENT = "IMPLEMENT"  # includes scope-expansion arbitration and fix iterations
    TEST = "TEST"
    QUALITY = "QUALITY"
    SECURITY = "SECURITY"
    REVIEW = "REVIEW"
    RELEASE = "RELEASE"
    GATE_MERGE = "GATE_MERGE"  # human approves merge, bound to the diff hash
    MERGED = "MERGED"
    GATE_DEPLOY = "GATE_DEPLOY"  # separate human approval for deployment
    DEPLOYED = "DEPLOYED"
    ESCALATED = "ESCALATED"  # bounded autonomy exhausted; a human must decide
    REJECTED = "REJECTED"  # human rejected at a gate
    ABORTED = "ABORTED"
    FAILED = "FAILED"  # unrecoverable system error


TERMINAL = frozenset({State.DEPLOYED, State.REJECTED, State.ABORTED, State.FAILED})
HUMAN_WAIT = frozenset({State.GATE_CRITERIA, State.GATE_MERGE, State.GATE_DEPLOY, State.ESCALATED})
GATES = frozenset({State.GATE_CRITERIA, State.GATE_MERGE, State.GATE_DEPLOY})

# Work states an ESCALATED run may be resumed into, by human decision.
RESUMABLE_WORK = frozenset(
    {State.ANALYSIS, State.DESIGN, State.IMPLEMENT, State.TEST, State.QUALITY, State.SECURITY, State.REVIEW}
)

_T: dict[State, set[State]] = {
    State.INTAKE: {State.ANALYSIS},
    State.ANALYSIS: {State.GATE_CRITERIA, State.DESIGN},
    State.GATE_CRITERIA: {State.DESIGN, State.ANALYSIS, State.REJECTED},
    State.DESIGN: {State.IMPLEMENT},
    State.IMPLEMENT: {State.TEST, State.DESIGN},
    State.TEST: {State.QUALITY, State.IMPLEMENT},
    State.QUALITY: {State.SECURITY, State.IMPLEMENT},
    State.SECURITY: {State.REVIEW, State.IMPLEMENT},
    State.REVIEW: {State.RELEASE, State.IMPLEMENT},
    State.RELEASE: {State.GATE_MERGE},
    State.GATE_MERGE: {State.MERGED, State.IMPLEMENT, State.REJECTED},
    State.MERGED: {State.GATE_DEPLOY},
    State.GATE_DEPLOY: {State.DEPLOYED, State.REJECTED},
    State.ESCALATED: set(RESUMABLE_WORK) | {State.REVIEW, State.RELEASE},
}

# Any non-terminal state may escalate, abort or fail.
for _s in State:
    if _s not in TERMINAL:
        _T.setdefault(_s, set())
        _T[_s] |= {State.ABORTED, State.FAILED}
        if _s not in GATES and _s not in {State.MERGED}:
            _T[_s].add(State.ESCALATED)
for _s in TERMINAL:
    _T[_s] = set()

TRANSITIONS: dict[State, frozenset[State]] = {k: frozenset(v) for k, v in _T.items()}

# Main-path order, used by the UI to draw the pipeline.
PIPELINE = [
    State.INTAKE,
    State.ANALYSIS,
    State.GATE_CRITERIA,
    State.DESIGN,
    State.IMPLEMENT,
    State.TEST,
    State.QUALITY,
    State.SECURITY,
    State.REVIEW,
    State.RELEASE,
    State.GATE_MERGE,
    State.MERGED,
    State.GATE_DEPLOY,
    State.DEPLOYED,
]


class IllegalTransition(RuntimeError):
    pass


def can_transition(src: State, dst: State) -> bool:
    return dst in TRANSITIONS[State(src)]


def assert_transition(src: State, dst: State) -> None:
    if not can_transition(src, dst):
        raise IllegalTransition(f"{State(src).value} -> {State(dst).value} is not allowed")
