import pytest

from services.agents.stalls import MODEL_TIMEOUT_S, StallCounter, StallRetryMiddleware, classify


def chained(outer: Exception, inner: Exception) -> Exception:
    try:
        try:
            raise inner
        except Exception as e:
            raise outer from e
    except Exception as e:
        return e


@pytest.mark.parametrize("error, kind", [
    (TimeoutError("read timed out"), "stall"),
    (RuntimeError("504 DEADLINE_EXCEEDED. {'error': {'code': 504, 'message': 'Deadline expired'}}"), "stall"),
    (chained(RuntimeError("model call failed"), TimeoutError()), "stall"),
    (RuntimeError("429 RESOURCE_EXHAUSTED. quota"), "transient"),
    (RuntimeError("Error calling model 'm' (UNAVAILABLE): 503 UNAVAILABLE. overloaded"), "transient"),
    (RuntimeError("400 INVALID_ARGUMENT. Thinking level MINIMAL is not supported"), None),
    (ValueError("read_file limit=500 exceeded"), None),
])
def test_classify(error, kind):
    assert classify(error) == kind


def test_timeout_is_above_the_api_minimum():
    assert 10 <= MODEL_TIMEOUT_S <= 15


def run(middleware, outcomes):
    calls = iter(outcomes)

    def handler(_request):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return middleware.wrap_model_call(None, handler)


def test_one_retry_then_success_or_give_up():
    counter = StallCounter()
    mw = StallRetryMiddleware("critic", counter)
    assert run(mw, ["ok"]) == "ok" and counter.snapshot() == (0, 0)
    assert run(mw, [TimeoutError(), "ok"]) == "ok" and counter.snapshot() == (1, 1)
    with pytest.raises(TimeoutError):
        run(mw, [TimeoutError(), TimeoutError(), "never reached"])
    assert counter.snapshot() == (3, 2) and counter.by_agent == {"critic": 3}
    assert run(mw, [RuntimeError("429 RESOURCE_EXHAUSTED. quota"), "ok"]) == "ok"
    assert counter.snapshot() == (3, 3)  # a transient error is retried but is not a stall
    with pytest.raises(ValueError):  # anything else is not retried
        run(mw, [ValueError("schema"), "never reached"])
    assert counter.snapshot() == (3, 3)
