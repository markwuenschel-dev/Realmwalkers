"""An embedding row must be labelled with the backend that ACTUALLY produced it.

`embed()` never raises — a provider error quietly returns a deterministic bag-of-words vector so
retrieval degrades instead of failing. That is right. What was wrong is that the write paths took
their `embedding_version` from `embedding_version()`, which reports what is CONFIGURED, so a
transient 429 mid-ingest produced rows holding hash vectors and stamped `openai:…`.

Nothing downstream could tell: retrieval applies no version filter, so a mislabelled row is ranked
by cosine distance against a vector space it does not belong to — noise shaped like a result — and
the staleness check exempts it because its label already matches the current backend. The row is
wrong permanently and invisibly, and no amount of later re-indexing finds it.
"""

from __future__ import annotations

from typing import Any

import pytest

from dominion.shared.config import settings
from dominion.workers.memory import embedding as emb


@pytest.fixture
def openai_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-a-real-key")
    monkeypatch.setattr(emb, "_warned_no_key", False)


def test_a_failed_call_is_labelled_hash_not_openai(openai_configured, monkeypatch) -> None:
    """The defect, stated as a test: a provider error must not produce an openai-labelled hash row."""

    def boom(_text: str) -> list[float]:
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(emb, "_openai_embed", boom)
    vec, version = emb.embed_with_version("the lamp went out at the third hour")

    assert version == emb.HASH_VERSION, "a hash vector must never be stamped as an OpenAI one"
    assert version != emb.embedding_version(), "the configured version is exactly what must NOT be used"
    assert vec == emb._hash_embed("the lamp went out at the third hour")


def test_a_successful_call_is_labelled_with_the_model(openai_configured, monkeypatch) -> None:
    monkeypatch.setattr(emb, "_openai_embed", lambda _t: [0.0] * emb.DIM)
    _, version = emb.embed_with_version("text")
    assert version == f"openai:{settings.embedding_model}"


def test_a_partly_failed_batch_labels_each_item_by_what_produced_it(openai_configured, monkeypatch) -> None:
    """The fallback is per BATCH, so one timeout in a rebuild leaves hash vectors among real ones. A
    single version for the whole call would mislabel whichever group it did not describe."""
    monkeypatch.setattr(emb, "_EMBED_BATCH", 2)
    seen: list[int] = []

    def flaky(texts: list[str]) -> list[list[float]]:
        seen.append(len(texts))
        if len(seen) == 2:  # the middle batch fails, the others succeed
            raise RuntimeError("timeout")
        return [[0.0] * emb.DIM for _ in texts]

    monkeypatch.setattr(emb, "_openai_embed_many", flaky)
    out = emb.embed_many_with_version(["a", "b", "c", "d", "e", "f"])

    versions = [v for _, v in out]
    real = f"openai:{settings.embedding_model}"
    assert versions == [real, real, emb.HASH_VERSION, emb.HASH_VERSION, real, real]
    assert len(out) == 6, "order and count must survive a partial failure"


def test_no_key_is_labelled_hash(monkeypatch) -> None:
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(emb, "_warned_no_key", False)
    assert emb.embed_with_version("text")[1] == emb.HASH_VERSION


def test_the_plain_helpers_still_return_bare_vectors(openai_configured, monkeypatch) -> None:
    """`embed`/`embed_many` are kept for the query path, where the vector is transient and never
    stored. They must keep their old shape or every retrieval call site breaks."""
    monkeypatch.setattr(emb, "_openai_embed", lambda _t: [0.5] * emb.DIM)
    monkeypatch.setattr(emb, "_openai_embed_many", lambda ts: [[0.5] * emb.DIM for _ in ts])
    assert emb.embed("q") == [0.5] * emb.DIM
    assert emb.embed_many(["a", "b"]) == [[0.5] * emb.DIM] * 2


def test_every_write_path_takes_its_version_from_the_embedder() -> None:
    """A guard against the defect coming back by a different door. Any module that persists
    `embedding_version=` must be reading it from the embed call, never from `embedding_version()` —
    which describes configuration, not what ran."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "dominion"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), start=1):
            # A stamp whose value is the configured version rather than the one just returned.
            if re.search(r"embedding_version\s*=\s*embedding_version\(\)", line):
                offenders.append(f"{path.relative_to(root)}:{n}")
    assert offenders == [], (
        f"these write the CONFIGURED backend onto a row instead of the one that actually ran: {offenders}"
    )


def test_stale_detection_still_compares_against_the_configured_backend() -> None:
    """`embedding_version()` keeps its job — deciding what needs re-embedding. Only stamping moved."""
    assert emb.embedding_version() == emb._configured_version()


def test_a_mislabelled_row_is_now_detectable_by_reading_the_label(openai_configured, monkeypatch) -> None:
    """The property that was missing: after a failure, the stored label differs from the configured
    one, so the existing staleness check flags the row and the next ingest re-embeds it."""
    monkeypatch.setattr(emb, "_openai_embed", lambda _t: (_ for _ in ()).throw(RuntimeError("down")))
    _, stored = emb.embed_with_version("text")
    assert stored != emb.embedding_version(), "a degraded row must read as stale, not as current"


def test_hash_vectors_are_unit_norm_or_zero() -> None:
    """Unchanged behaviour, pinned because the fallback is now load-bearing in more places."""
    import math

    vec: Any = emb._hash_embed("some words here")
    assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-9
    assert emb._hash_embed("   ") == [0.0] * emb.DIM
