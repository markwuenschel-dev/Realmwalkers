# Realmwalkers

Realmwalkers is a contract-first novel-writing system where author-approved story contracts, scene prose, editorial review, and production assembly stay explicit and human-gated.

## Language

**Production Run**:
An editorial pass over one chapter that assembles scene prose into a final chapter candidate, records QA findings, and tracks repair work until the author approves or stops it.
_Avoid_: pipeline, batch, release run

**Production Run Facade**:
The single public production-run module interface used by routers, background workers, and tests to create, inspect, triage, repair, assemble, and approve a Production Run.
_Avoid_: lane router, production service, workflow API
