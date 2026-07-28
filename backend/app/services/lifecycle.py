"""Document status state machine.

    queued ──► processing ──► needs_review ──► approved ──► pushed
       ▲            │              │  ▲           │  │         │
       │            └──► failed ◄──┘  └───────────┘  └─────────┘
       └────────────── failed ──► queued (re-process)

Editing an already-approved document returns it to needs_review (data changed,
must be re-reviewed before re-push).
"""
from typing import Dict, Set

QUEUED = "queued"
PROCESSING = "processing"
NEEDS_REVIEW = "needs_review"
APPROVED = "approved"
PUSHED = "pushed"
FAILED = "failed"

ALL_STATUSES = {QUEUED, PROCESSING, NEEDS_REVIEW, APPROVED, PUSHED, FAILED}

_ALLOWED: Dict[str, Set[str]] = {
    QUEUED: {PROCESSING, FAILED},
    PROCESSING: {NEEDS_REVIEW, FAILED},
    NEEDS_REVIEW: {NEEDS_REVIEW, APPROVED, FAILED},
    APPROVED: {PUSHED, NEEDS_REVIEW},
    PUSHED: {PUSHED, NEEDS_REVIEW},  # re-push is idempotent; edits reopen review
    FAILED: {QUEUED},
}


def can_transition(current: str, target: str) -> bool:
    return target in _ALLOWED.get(current, set())


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str):
        super().__init__(f"Cannot transition document from '{current}' to '{target}'.")
        self.current = current
        self.target = target


def ensure_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
