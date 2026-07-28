"""Document status state machine."""
import pytest

from app.services import lifecycle as lc


@pytest.mark.parametrize(
    "current,target",
    [
        (lc.QUEUED, lc.PROCESSING),
        (lc.PROCESSING, lc.NEEDS_REVIEW),
        (lc.NEEDS_REVIEW, lc.APPROVED),
        (lc.APPROVED, lc.PUSHED),
        (lc.APPROVED, lc.NEEDS_REVIEW),
        (lc.FAILED, lc.QUEUED),
        (lc.NEEDS_REVIEW, lc.NEEDS_REVIEW),
    ],
)
def test_allowed_transitions(current, target):
    assert lc.can_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (lc.QUEUED, lc.APPROVED),
        (lc.PROCESSING, lc.PUSHED),
        (lc.NEEDS_REVIEW, lc.PUSHED),
        (lc.FAILED, lc.APPROVED),
        (lc.PUSHED, lc.APPROVED),
    ],
)
def test_forbidden_transitions(current, target):
    assert not lc.can_transition(current, target)


def test_ensure_transition_raises():
    with pytest.raises(lc.InvalidTransition):
        lc.ensure_transition(lc.QUEUED, lc.APPROVED)
