"""Inbound ASGI smoke test (HTTP-HARNESS).

The first real inbound HTTP round-trip against the FastAPI app. Until this file, every one of the
~25 routers was tested only as a plain async coroutine, so FastAPI routing, `Depends` injection,
request-body validation (422s), CORS, exception handling, response serialization, and the lifespan
boot had NO red-capable check. These tests drive the app through `httpx.ASGITransport` via the
`app_client` fixture (tests/conftest.py) and assert on the wire.

Requires a reachable Postgres (see `db_factory`): locally the DB tests skip when Postgres is down; in
CI `DOMINION_REQUIRE_DB=1` makes an unreachable DB fail loudly.
"""

from __future__ import annotations

import httpx
import pytest

# A representative GET per router group. Each must return 200 against an empty (truncated) test DB —
# proof that routing + `Depends(SessionDep)` injection + the DB round-trip + response_model
# serialization all run end to end through the ASGI stack. The nil UUID exercises query-param parsing
# on a router whose list endpoint is book-scoped.
_NIL_UUID = "00000000-0000-0000-0000-000000000000"
_REPRESENTATIVE_GETS = [
    "/health",  # health router
    "/books",  # books router
    "/activity",  # activity router
    "/scenes/pending",  # scenes router
    "/jobs/status",  # jobs router
    "/settings/models",  # settings router
    f"/chapters?book_id={_NIL_UUID}",  # chapters router (query-param parsing)
]


async def test_harness_present_and_drives_real_app(app_client: httpx.AsyncClient) -> None:
    """CAN'T-ROT-BACK GATE.

    If the `app_client` ASGI fixture is removed from tests/conftest.py, this test (and the whole
    file) errors with "fixture 'app_client' not found" and CI goes red — the inbound HTTP harness
    cannot silently regress back to plain-coroutine testing. Also confirms, behaviorally, that the
    client drives the real dominion ASGI app (a blank ASGI app would 404 `/books`).
    """
    assert isinstance(app_client, httpx.AsyncClient)
    health = await app_client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    books = await app_client.get("/books")
    assert books.status_code == 200, books.text


@pytest.mark.parametrize("path", _REPRESENTATIVE_GETS)
async def test_representative_get_ok(app_client: httpx.AsyncClient, path: str) -> None:
    """One GET per router group returns 200 through the real ASGI round-trip."""
    resp = await app_client.get(path)
    assert resp.status_code == 200, f"GET {path} -> {resp.status_code}: {resp.text[:300]}"


async def test_post_invalid_body_is_422(app_client: httpx.AsyncClient) -> None:
    """Request-body validation is live: POST /books requires `title` (schemas.BookIn); omitting it
    must fail validation with a 422 BEFORE any handler code runs. A plain-coroutine test of the
    handler would never see this — Pydantic request validation is a routing-layer behavior."""
    resp = await app_client.post("/books", json={"premise": "missing the required title"})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert any(err["loc"][-1] == "title" for err in detail), detail


async def test_cors_middleware_is_wired(app_client: httpx.AsyncClient) -> None:
    """CORS middleware runs on the stack. A CORS preflight (OPTIONS + Origin +
    Access-Control-Request-Method) is intercepted by CORSMiddleware, not routed to a handler: with the
    default empty allow-origins it answers 400 "Disallowed CORS origin". A plain OPTIONS (no preflight
    headers) instead falls through to the router and 405s (no OPTIONS handler on /books). The
    difference proves the CORS layer is installed and executing."""
    plain = await app_client.options("/books")
    assert plain.status_code == 405, plain.text
    preflight = await app_client.options(
        "/books",
        headers={"Origin": "http://example.test", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 400
    assert "Disallowed CORS origin" in preflight.text


async def test_lifespan_boot_runs_against_test_db(app_client: httpx.AsyncClient, db_factory) -> None:
    """Entering `app_client` booted the app's lifespan (main.lifespan) against the test DB. Its boot
    integrity probe writes a `JobIntegrityState` singleton (id=1) on first boot when no holds exist;
    reading it back through the SAME test factory proves startup ran and wrote to the test database —
    not a mocked stand-in."""
    from dominion.shared.models import JobIntegrityState

    async with db_factory() as session:
        state = await session.get(JobIntegrityState, 1)
    assert state is not None, "lifespan boot did not run / did not write JobIntegrityState(id=1)"
