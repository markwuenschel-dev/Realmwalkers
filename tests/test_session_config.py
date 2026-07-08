"""Pin the load-bearing async-session invariant behind the N1/C1 greenlet class.

`expire_on_commit` must stay False: routers and workers read ORM attributes after `commit()` (enrich ->
`model_validate(row)`), and an async session cannot lazy-load an expired attribute — that sync IO raises
`MissingGreenlet` (the N1/C1 500 class). Flipping this to SQLAlchemy's default resurrects the class
fleet-wide, so the invariant is asserted here instead of left implicit at the config line. Pure config
check — no database required.
"""

from __future__ import annotations

from dominion.shared.db import SessionFactory


def test_session_factory_does_not_expire_on_commit():
    assert SessionFactory.kw.get("expire_on_commit") is False
