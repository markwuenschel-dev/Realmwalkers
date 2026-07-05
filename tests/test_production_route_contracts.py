"""Route-contract regression: the optional-body decision endpoints must accept a bodyless POST.

The frontend posts `/repair-tasks/{id}/approve-apply` with `Content-Type: application/json` and NO
body (see `client.ts`). These handlers typed `body: IssueDecisionIn | None` WITHOUT a default, which
makes FastAPI mark the request body *required* → every Approve & apply returned 422 and surfaced as a
red banner. The worker-level tests (`test_repair_tasks.py`) call the functions directly and never
exercised the HTTP contract, so the bug was latent. This pins the contract via the generated OpenAPI
schema (no DB, no network): the affected endpoints must NOT require a body; `merge` still must.
"""

from __future__ import annotations

import pytest

from dominion.api.main import app


def _request_body_required(path: str) -> bool:
    op = app.openapi()["paths"][path]["post"]
    rb = op.get("requestBody")
    # No requestBody at all, or one explicitly marked optional, both mean a bodyless POST is allowed.
    return bool(rb and rb.get("required", False))


@pytest.mark.parametrize(
    "path",
    [
        "/repair-tasks/{task_id}/approve-apply",  # the user-visible "Approve & apply"
        "/repair-tasks/{task_id}/reject",
        "/repair-tasks/{task_id}/rollback",
        "/issues/{issue_id}/accept",
        "/issues/{issue_id}/reject",
        "/issues/{issue_id}/escalate",
        "/issues/{issue_id}/mark-false-positive",
    ],
)
def test_decision_endpoints_accept_bodyless_post(path: str) -> None:
    assert _request_body_required(path) is False, f"{path} still requires a body → a bodyless POST 422s"


def test_merge_issue_still_requires_a_body() -> None:
    # Negative control: merge genuinely needs `merged_into_issue_id`, so its body stays required.
    assert _request_body_required("/issues/{issue_id}/merge") is True
