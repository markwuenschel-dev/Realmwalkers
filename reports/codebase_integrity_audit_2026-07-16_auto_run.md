# Integrity audit — auto-run outcome (2026-07-16)

`/codebase-integrity-audit-loop --auto --max-turns max`, resuming from
[`codebase_integrity_audit_2026-07-16.html`](codebase_integrity_audit_2026-07-16.html). Work done on
`main`, committed locally, **not pushed** (shipping awaits an explicit "ship it"). No candidate that
requires a human decision was executed.

## Completed (4 turns)

| ID | Branch | Change | Commit | Verification |
|----|--------|--------|--------|--------------|
| **A1a** | direct-fix | Marked the 6 grep-verified-unbuilt ADR-0030/0031 terms in `CONTEXT.md` `_(planned)_` + a status banner naming the live mechanism (`autonomy_enabled` KV flag, mutable `RepairTask` fields). `Job Book Ownership` (ADR-0027, wired) left unmarked. | `628db6c` | 6 markers on the intended terms; no code parses `CONTEXT.md`; +17/−6. |
| **N1** | direct-fix | Declared `jszip@^3.10.1` in `frontend/devDependencies` (was an undeclared direct import resolved only via `.npmrc public-hoist-pattern[]=*`). Lockfile updated with the CI-pinned pnpm 10.11.0 (3-line importer add). | `a318c9e` | local `tsc` green; `vitest` 35/35 suites, 345 tests (docxXml suite un-redded). |
| **N2** | fitness-check | Backend parity test pinning the Enrich panel's hardcoded `LANES` to the backend canonical lanes (`passes_for` order); catches a 4th-pass drift. Endpoint + UI untouched. | `0de486a` | `test_enrich_lane_parity.py` 2 passed (incl. red-capability proof); ruff clean. |
| **NC1b** | fitness-check | Enforced the greenlet guard's cross-module bound: a test fails if `begin_nested` appears outside `sweeper.py`. (The function-level generalization beyond `_sweep_one_run` was already shipped pre-run.) | `2934a1b` | guard file 3 passed; `begin_nested` confirmed confined to `sweeper.py`. |

Final verification (whole repo): **backend 769 passed / 1 xfail**, ruff + pyright(src) clean, frontend
`tsc`/`vitest`(345)/`oxlint`/`oxfmt` green.

## Deferred — stayed in the ledger (not auto-executable this run)

| ID | Reason not auto-executed |
|----|--------------------------|
| **C6** (tests/ pyright) | Pre-check: **99 pyright errors** in `tests/`. Typing it clean is broad cleanup (forbidden) or needs a baseline mechanism (design decision). |
| **C8** (coverage tooling) | Spans two seams (backend `pytest-cov` + frontend `vitest --coverage`) + CI, and a real gate needs a coverage-policy/threshold decision. |
| **C14** (migration forward-parity) | Needs a schema-baseline mechanism (design decision); touches the migration category, and a red result is a migration reconcile. |
| **C7** (action_kind/kind → StrEnum) | Cross-language/public-contract change (OpenAPI + generated.ts + TS consumers), coupled to C4's parity gate. |
| **C15** (severity `hard`↔`block`) | Requires a data backfill of live rows → migration/backfill category. |
| **C3** (nested-JSONB fixture-diff) | 46 fields / 194 interfaces — scope balloon; a fixture-diff that reveals drift is a contract reconcile. |
| **C5** (production.py god-function) | Architecture-direction decision (how to decompose ~357 lines) — design branch. |
| **C4** (enum TS-union gen + DB CHECK) | Migration (DB CHECK) + generated-contract + design. |
| **NC1c** (serialize-safe seam) | Architecture-direction decision (where the "row is serialization-ready after commit" invariant lives) — design branch. |

## Blocked — needs a human decision (never selectable in auto)

- **A1b** — may a `human_required` repair ever be auto-approved when the ceiling is raised? `sweeper.py:59-60` ("must never") vs `:167-171` ("opts into full autonomy") contradict; ADR-0031 D16 decided to retire the rung but code didn't. Reconcile + pin the safe default.
- **C10** — is the API meant to be publicly reachable, or must it sit behind auth? (fully unauthenticated today.)
- **C2** — one approval policy over two tiers, or two intentional policies? Needs an ADR.
- **A1c** — build the autonomy subsystem (epoch/grants/holds) per ADR-0030/0031, or accept the KV-flag model as sufficient.

## New finding (unverified lead for the next run)

- **Do the ADR-0028 terms in `CONTEXT.md` match implementation?** Import Adoption, Revision Request,
  Chapter Workflow Lock, Import Scene Evidence are stated present-tense; ADR-0031 self-notes the
  ADR-0028 layer as "inert". **Not verified this run** — A1a was scoped to the ADR-0030/0031 safety
  surface. Verify the 0028 code state before relabeling.

## Next

Nothing pushed. Review commits `628db6c..2934a1b` and `git push` when ready (or say "ship it").
Blocked forks (A1b/C10/C2/A1c) want your decision; the deferred design/migration candidates
(C14/C7/C3/C5/C4/NC1c/C15) are each a deliberate next loop.
