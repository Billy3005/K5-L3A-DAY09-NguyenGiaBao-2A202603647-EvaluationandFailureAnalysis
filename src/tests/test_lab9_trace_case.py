"""Regression tests from docs/trace_case.md: a failing trial found in a report becomes a test.

Each test runs one real claim@seed on a fresh temp store (no report, no team DB) and asserts the
grader that names the first wrong step fires on the buggy agent and stays quiet on `fixed`.
"""
import pytest

pytestmark = pytest.mark.student


@pytest.fixture(autouse=True, scope="module")
def _restore_shape_registry():
    """investigate() registers the lab9 variants (and these tests a probe shape) into Lab 8's global
    SHAPES; put the registry back so Lab 8 contract tests in the same process see only their shapes."""
    from lab8.shapes import SHAPES

    saved = dict(SHAPES)
    yield
    SHAPES.clear()
    SHAPES.update(saved)


def _graded(shape: str, claim: str, seed: int, bugs: frozenset[str] | None = None):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders
    from lab9.trace_case import investigate

    if bugs is not None:                     # a one-bug probe shape, registered in memory only
        from lab8.shapes import SHAPES
        from lab9.variant_agent import make_shape

        SHAPES.setdefault(shape, make_shape(shape, bugs))
    case = investigate(shape, claim, seed)
    ctx = EvaluatorContext(name=f"{shape}:{claim}@{seed}", inputs={}, metadata=None,
                           expected_output=case["label"], output=case["trial"], duration=0.0,
                           _span_tree=None, attributes={}, metrics={})
    return case["trial"], {name: cls().evaluate(ctx).value for name, cls in graders.GRADERS.items()}


def test_lab9_trace_case_fm03_ticket_over_db():
    """seeded_regression C-002@11: step 3 `model_override {delay_days: 5}` replaces the DB's 2 days."""
    trial, g = _graded("seeded_regression", "C-002", 11)
    kinds = [e["kind"] for e in trial["events"]]
    assert kinds.index("model_override") == 2           # event 3: the first wrong step
    assert kinds[:2] == ["handoff", "investigator"]     # steps 1-2 read the DB correctly
    assert g["db_fact_kept"] is False and g["refund_amount_correct"] is False and g["pass_rate"] is False

    trial, g = _graded("fixed", "C-002", 11)
    assert "model_override" not in [e["kind"] for e in trial["events"]]
    assert g["db_fact_kept"] is True and g["refund_amount_correct"] is True and g["pass_rate"] is True


def test_lab9_trace_case_fm08_reply_states_unbooked_amount():
    """FM-08: C-008@11 the host books 20.000đ (approval cap) but a misreporting agent says 35.000đ.
    The Lab 8 grader passes this trial; only reply_matches_ledger catches it."""
    trial, g = _graded("probe_misreport", "C-008", 11, frozenset({"no_override", "misreport"}))
    assert trial["snapshot"]["refund_vnd"] == 20000
    assert g["pass_rate"] is True                        # the outcome grader is blind to it
    assert g["reply_matches_ledger"] is False

    _, g = _graded("fixed", "C-008", 11)
    assert g["reply_matches_ledger"] is True
