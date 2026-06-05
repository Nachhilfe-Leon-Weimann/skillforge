# ADR 0001 - `openapi.json` as the versioned API contract

Status: Accepted, 2026-05

## Context

SkillForge is the backend; SkillBot (and potentially other consumers) speak its REST API.
Without a pinned contract, server and client drift apart: a renamed field or changed status code
only surfaces at runtime. A hand-maintained SDK would be extra, quickly-stale maintenance burden
alongside the actual code.

## Decision

The **`openapi.json` generated from FastAPI is the contract** and is **committed to the repo**.

- `just openapi` regenerates it via `scripts/dump_openapi.py` (imports the app with dummy
  settings, no DB connection needed).
- `just openapi-check` (part of `just check`, runs in CI) fails if the committed file differs
  from the current API state - so drift can't be missed.
- Consumers **generate their client from `openapi.json`** instead of maintaining a hand-written
  SDK.

Deliberately *not*: no custom SDK, no hosted doc portal.

## Consequences

- The contract is always current and machine-readable; breaking changes show up in the diff.
- `openapi.json` is **never edited by hand** - it's generated. Changes happen through code
  changes + `just openapi`.
- One extra CI step; a minimal price for guaranteed contract fidelity.

Introduced in PR #34.
