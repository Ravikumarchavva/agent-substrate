import pickle

from loamz.engine.errors import (
    NotFoundError,
    QuotaExceededError,
    RateLimitedError,
    ScopeViolationError,
    http_status,
)


def test_codes_derive_from_names_and_survive_pickling():
    error = ScopeViolationError("taken", record_id="r1")
    copy = pickle.loads(pickle.dumps(error))
    assert copy.code == "scope-violation" and copy.record_id == "r1" and str(copy) == "taken"
    assert RateLimitedError(retry_after=3).retryable


def test_each_error_has_one_http_status():
    assert http_status(NotFoundError("x")) == 404
    assert http_status(QuotaExceededError("full", limit=10)) == 413
    assert http_status(ValueError("bug")) == 500
