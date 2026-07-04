# Provider rate-limit handling — Lane 7 recovery report

Date: 2026-07-04. Branch: `recovery/l7-rate-limit` (baseline `4b9b282`).
Mission: a provider 429 is retryable PROVIDER state on every path — never an author, QA, or
contract failure. Pinned vocabulary: issue/problem kind **`infra_rate_limit`**, production-run
stage **`provider_rate_limited`**, scene-packet status **`rate_limited`** (pre-existing).

## 1. Audit — where a 429 / `LlmRateLimited` can surface, and what happened before

| # | Path | Before | Verdict |
|---|------|--------|---------|
| 1 | `llm.complete` / `count_tokens` retry loop (`workers/llm.py::_call_with_retries`) | Already correct: exponential backoff + full jitter, floored at the provider's Retry-After / `x-ratelimit-reset-*` hint, capped at `llm_retry_max_delay_s`; exhausted 429 raises typed `LlmRateLimited` (with `retry_after_s`, `attempts`); failed calls telemetry-recorded with `retries` + `rate_limit_headers`. | OK (verified, extended telemetry only) |
| 2 | Scene draft/revision job (`worker.run_once` → `pipeline.generate_one_scene` → drafter) | `LlmRateLimited` propagated unwrapped, so `last_error` happened to start `"LlmRateLimited: …"`, but nothing guaranteed it (a wrapped 429 lost the prefix), no machine-readable kind, and diagnostics lumped it under `failed_draft_job` severity=error ("fix root cause"). | **Misclassified — fixed** |
| 3 | Advisory reviewers (`pipeline` gather) | A 429'd reviewer landed as `Critique(reviewer=<name>, note="reviewer failed: …")`; production's issue snapshot then minted it as a LITERARY issue (`Issue.issue_kind = reviewer name`, e.g. `pacing`) feeding the §5 repair swarm. | **Misclassified — fixed** |
| 4 | Enrichment passes (`PassError` wrap) | Same as #3 via `"enrichment pass failed: …"` critiques (429 buried in the `PassError.__cause__`). | **Misclassified — fixed** |
| 5 | Escalation (`llm_escalation.attempt_with_escalation`) | Primary 429 → propagates typed (correct). But a **fallback** 429 during a *semantic* escalation threw away a usable primary result — provider state converted into an apparent author failure. | **Partially misclassified — fixed** |
| 6 | Scene-packet derive, author call (`scene_packet/derive.py::_author_then_qa`) | Already correct: `blocker_source="rate_limit"` → `status_after_author_qa` → `RATE_LIMITED` (never `BLOCKED`/author). | OK (verified + tests) |
| 7 | Scene-packet derive, QA call | Already correct: valid body is persisted, packet lands `RATE_LIMITED` with "re-run QA" remedy. | OK (verified + tests) |
| 8 | Manual QA rerun (`POST /scene-packets/{id}/qa`) | Already correct: `LlmRateLimited` → HTTP 429, packet untouched (never `apply_qa_rerun(None)` → blocked). | OK (verified + tests) |
| 9 | Production-run scene jobs (repair `apply` → revision job; `draft-missing-scenes` → draft job) | All production LLM work flows through the job path (#2). A 429'd job went `FAILED` with no run-level signal — the run sat on `awaiting_scene_drafts`/`repair_execution` looking broken. | **Unclassified — fixed** |
| 10 | `retry-failed` requeue (`draft_queue.reconcile_and_requeue_failed_draft_jobs`) | Already free: requeues every FAILED draft job with fresh contract resolution; no filter keys on `last_error`, so rate-limited jobs were and are freely requeueable. | OK (verified) |
| 11 | `PromptBudgetExceeded` preflight | Local policy gate, zero provider traffic; classified `validation`, HTTP 422 on manual QA. Correctly NOT a rate limit. | OK |

Out of lane (noted, untouched): chapter-packet derive (`workers/packet/*`) and the embedding
provider (`workers/memory/embedding`) have their own error paths; neither routes a 429 into the
scene-packet/job vocabulary above.

## 2. Patches

- **`workers/llm.py`** — added `find_rate_limit(exc)`: walks `__cause__`/`__context__` for an
  `LlmRateLimited`, so orchestrators classify by type even when the 429 arrives wrapped
  (PassError, re-raise). Added to the `llm.complete` structlog event (already rich in cache/token
  fields): `retries`, `max_tokens`, `requested_tokens`, `rate_limit_headers` (when the
  OpenAI-compatible path returns them). Telemetry metadata already carried all of these plus
  `retry_after_s`/`rate_limited` on failures — no DB migration, no new columns.
- **`workers/worker.py`** — pinned vocabulary constants + `classify_job_failure(exc, loc)`:
  any failure with a 429 in its chain records `last_error` prefixed `"LlmRateLimited: …"` and kind
  `infra_rate_limit`; `scene.failed` logs `error_kind`. When the failed job belongs to a
  production run, the run's `current_stage` is parked on **`provider_rate_limited`** (plain stage
  string, no migration) instead of a state that reads as a pipeline failure. Ordinary failures keep
  the exact previous `"<Type>: <msg> @ file:line"` shape.
- **`workers/llm_escalation.py`** — a fallback 429 no longer discards a usable, untruncated
  primary result (semantic escalation returns the primary; logged
  `llm_escalation.fallback_rate_limited_kept_primary`). With no usable primary the
  `LlmRateLimited` propagates typed, never swallowed.
- **`workers/pipeline.py`** *(declared, beyond named lane files)* — reviewer/enrichment failure
  critiques whose chain contains `LlmRateLimited` now carry `payload={"kind": "infra_rate_limit"}`
  and a "rate limited by provider (retryable, not a prose defect)" note. Production's issue
  snapshot uses `payload["kind"]` as `Issue.issue_kind`, so a 429 can no longer be minted as a
  `pacing`/`continuity`/`combat` literary issue and spawn repair tasks.
- **`workers/telemetry_diagnostics.py`** *(declared)* — new `detect_rate_limited_jobs` problem:
  kind `infra_rate_limit`, severity `warn`, action "requeue via Retry failed once the limit
  resets". `detect_failed_jobs` (kind `failed_draft_job`, severity `error`) now excludes them, so
  "failed draft job" again means "needs a root-cause fix".

## 3. What was already there (extended, not duplicated)

- `LlmRateLimited`, `_call_with_retries` (backoff + jitter + Retry-After floor/cap),
  `PromptBudgetExceeded`, per-call telemetry incl. failed-call records — `workers/llm.py`.
- `ScenePacketStatus.RATE_LIMITED` + `blocker_source="rate_limit"` machinery —
  `scene_packet/approval_policy.py`, `scene_packet/derive.py`; QA-rerun release of a
  rate-limited hold; HTTP 429 on the manual QA route.
- Provider concurrency semaphores: `llm_openai_concurrency=1` (scene author/QA path — kept at 1;
  gpt-mini TPM windows self-inflict 429s under concurrency), `llm_anthropic_concurrency=0`
  (uncapped; `scene_packet_max_inflight_llm` bounds the derive fan-out). Semaphore is held across
  the whole retry loop by design. **Verified defaults unchanged.**

## 4. Tests (`tests/test_rate_limit_handling.py` — deterministic; no network, no Postgres)

- Vocabulary pins (`infra_rate_limit`, `provider_rate_limited`, `rate_limited`).
- `find_rate_limit`: direct, `raise … from`, implicit `__context__`, negative.
- `classify_job_failure`: prefix + kind for direct and wrapped 429s; ordinary errors unchanged.
- Diagnostics: rate-limited jobs → `infra_rate_limit` (warn), excluded from `failed_draft_job`;
  all-rate-limited leaves no `failed_draft_job` problem.
- Backoff honors Retry-After (floor) and caps a 60s hint at `llm_retry_max_delay_s` (mocked sleep).
- Escalation: semantic-fallback 429 keeps usable primary; structural-fallback 429 propagates typed.
- Derive: author-429 → `blocker_source="rate_limit"` → `RATE_LIMITED` (never author-blocked);
  QA-429 keeps the valid body → `RATE_LIMITED`; successful QA rerun releases the hold.
- Manual QA rerun route: HTTP 429, packet + verdict untouched, nothing committed.

Complements the pre-existing `tests/test_llm_rate_limit.py` (retry-loop mechanics, header parsing,
provider-slot caps).

## 5. Follow-ups (out of lane)

- Desk UI: surface the `infra_rate_limit` problem kind and the `provider_rate_limited` run stage
  with a one-click "retry when reset" affordance (L8 owns gate diagnostics UI).
- A production run parked on `provider_rate_limited` is un-parked by the next successful
  scene job (`update_timeline_after_scene` / normal stage progression); an explicit
  resume-on-retry-failed hook could tighten that loop.
- `worker.run_once`'s wall clock (`scene_time_budget_s`) can convert a long Retry-After backoff
  into a `TimeoutError` (classified as ordinary failure). Retry-After is capped at
  `llm_retry_max_delay_s=30s` across ≤3 retries, so the window is small; noted, not changed.
