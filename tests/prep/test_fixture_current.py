"""The committed fixture book is what its generator writes."""

from __future__ import annotations

from tests.prep.fixture import generate


def test_the_committed_fixture_is_what_the_generator_writes():
    """``generate.py --check`` said so only when somebody ran it, and nobody did: the files had drifted."""
    assert generate.check(generate.DEFAULT_OUT) == []
    assert generate.main(["--check"]) == 0
