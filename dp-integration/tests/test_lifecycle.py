import pytest

from dp_integration.lifecycle import (DATA_CONTRACT_TRANSITIONS, DATA_PRODUCT_TRANSITIONS, TransitionError,
                                      at_least, walk)


@pytest.mark.parametrize("current,target,steps", [
    ("draft", "draft", []),
    ("draft", "active", ["proposed", "approved", "active"]),
    ("proposed", "approved", ["approved"]),
    ("active", "retired", ["deprecated", "retired"]),
    ("deprecated", "active", ["active"]),
])
def test_contract_walk(current, target, steps):
    assert walk(current, target, DATA_CONTRACT_TRANSITIONS) == steps


@pytest.mark.parametrize("current,target,steps", [
    ("draft", "active", ["proposed", "under_review", "approved", "active"]),
    ("draft", "under_review", ["proposed", "under_review"]),
    ("approved", "draft", ["draft"]),
    ("draft", "deprecated", ["deprecated"]),
])
def test_product_walk(current, target, steps):
    assert walk(current, target, DATA_PRODUCT_TRANSITIONS) == steps


def test_walk_never_passes_through_deprecated():
    for target in ("proposed", "under_review", "approved", "active"):
        assert "deprecated" not in walk("draft", target, DATA_PRODUCT_TRANSITIONS)


@pytest.mark.parametrize("current,target", [("retired", "active"), ("active", "draft"), ("draft", "bogus")])
def test_impossible_walk(current, target):
    with pytest.raises(TransitionError):
        walk(current, target, DATA_PRODUCT_TRANSITIONS)


def test_at_least():
    assert at_least("active", "approved")
    assert at_least("approved", "approved")
    assert not at_least("proposed", "approved")
