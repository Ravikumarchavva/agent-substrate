"""The client's errors are keyed by the same problem codes the server emits, and its tenancy comes from loamz.server.identity."""

import pytest

from loamz.core.tenancy import TenancyError
from loamz.data import errors, locate


@pytest.mark.parametrize(
    ("code", "status", "cls"),
    [
        ("not-found", 404, errors.NotFoundError),
        ("forbidden", 403, errors.ForbiddenError),
        ("insufficient-scope", 403, errors.InsufficientScopeError),
        ("conflict", 409, errors.ConflictError),
        ("scope-violation", 409, errors.ScopeViolationError),
        ("precondition-failed", 412, errors.PreconditionFailedError),
        ("quota-exceeded", 413, errors.QuotaExceededError),
        ("vector-space", 422, errors.VectorSpaceError),
        ("rate-limited", 429, errors.RateLimitedError),
        ("unavailable", 503, errors.ServiceUnavailableError),
        ("unauthenticated", 401, errors.UnauthenticatedError),
        ("invalid-request", 422, errors.InvalidRequestError),
    ],
)
def test_a_problem_maps_to_the_class_of_its_code(code, status, cls):
    error = errors.error_for(status, {"type": f"https://loamz.com/problems/{code}", "detail": "d"})
    assert type(error) is cls and error.code == code and error.detail == "d"


def test_an_unknown_code_falls_back_to_the_status():
    assert type(errors.error_for(404, {"type": "x/whatever"})) is errors.NotFoundError
    assert type(errors.error_for(418, {})) is errors.DataError


def test_a_key_locates_in_the_tenants_workspace_volume():
    assert locate("tenants/Acme-Corp/users/u1/a.txt") == (
        "tenant_acme_corp.workspace.files",
        "users/u1/a.txt",
    )
    with pytest.raises(TenancyError):
        locate("tenants/t/../other/a.txt")
