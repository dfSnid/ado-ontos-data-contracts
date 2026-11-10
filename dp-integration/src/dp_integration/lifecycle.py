"""Ontos v1.0.2 lifecycle transitions, and the shortest walk from one status to another.

Ontos only moves an entity one allowed step at a time (draft cannot go straight to active), while
the file in Git says where the entity should end up. The sync walks the steps in between, so a
PR may move a product or contract several steps at once. Copied from Ontos models/lifecycle.py;
check them again when Ontos is upgraded.
"""
from __future__ import annotations

from collections import deque
from typing import Mapping, Sequence

Transitions = Mapping[str, Sequence[str]]

DATA_PRODUCT_TRANSITIONS: Transitions = {
    "draft": ["sandbox", "proposed", "deprecated"],
    "sandbox": ["draft", "proposed", "deprecated"],
    "proposed": ["draft", "under_review", "deprecated"],
    "under_review": ["draft", "approved", "deprecated"],
    "approved": ["active", "draft", "deprecated"],
    "active": ["deprecated"],
    "deprecated": ["retired", "active"],
    "retired": [],
}

DATA_CONTRACT_TRANSITIONS: Transitions = {
    "draft": ["proposed", "deprecated"],
    "proposed": ["draft", "under_review", "approved", "deprecated"],
    "under_review": ["draft", "approved", "deprecated"],
    "approved": ["active", "draft", "deprecated"],
    "active": ["deprecated"],
    "deprecated": ["retired", "active"],
    "retired": [],
}

# Never passed through on the way to another status: deprecated -> active is a real Ontos step,
# so the shortest walk from draft to active would otherwise deprecate the entity first.
NOT_VIA = frozenset({"sandbox", "deprecated", "retired"})

# Forward order of the statuses, for "at least approved" style checks.
ORDER = ("draft", "sandbox", "proposed", "under_review", "approved", "active", "deprecated", "retired")


class TransitionError(ValueError):
    """No sequence of allowed steps leads from one status to the other."""


def walk(current: str, target: str, transitions: Transitions) -> list[str]:
    """The statuses to pass through, in order, ending with `target`; [] if already there."""
    if current == target:
        return []
    if current not in transitions or target not in transitions:
        raise TransitionError(f"unknown status '{current if current not in transitions else target}'")
    blocked = {"sandbox"} if target in ("deprecated", "retired") else NOT_VIA
    previous: dict[str, str] = {current: ""}
    queue = deque([current])
    while queue:
        status = queue.popleft()
        for step in transitions[status]:
            if step in previous:
                continue
            previous[step] = status
            if step == target:
                path = [step]
                while previous[path[-1]] != current:
                    path.append(previous[path[-1]])
                return path[::-1]
            if step not in blocked:
                queue.append(step)
    raise TransitionError(f"Ontos allows no way from '{current}' to '{target}'")


def at_least(status: str, floor: str) -> bool:
    """True when `status` is `floor` or later in the lifecycle."""
    return ORDER.index(status) >= ORDER.index(floor)
