# SDK Secrets + Runtime Env Parity Plan

## Context

This plan closes drift between:

- `ara-python-sdk` (public package used by customers), and
- monorepo App SDK runtime/docs (`/Users/sve/ara`) where secrets/runtime env support is already implemented and documented.

The immediate objective is to make `ara-python-sdk` support:

1. declarative secrets helpers,
2. runtime `env` + `secrets` fields,
3. deploy-time secret sync against `/apps/{app_id}/secrets`,
4. CLI parity and docs parity with current backend behavior.

## Verified Backend Reality (Current State)

Backend support already exists and is production-shaped:

- **Encrypted secrets table exists** in migration:
  - `Ara-backend/supabase/migrations/20260408143000_add_app_secrets.sql`
  - Table: `public.app_secrets`
  - Column: `encrypted_payload` (`TEXT NOT NULL`)
  - Includes RLS policies and metadata-only read grants for authenticated users.
- **Encryption is implemented** with AES-256-GCM:
  - `Ara-backend/api/services/crypto.py`
  - `encrypt()` / `decrypt()` using `ENCRYPTION_KEY`.
- **Secret validation + runtime env composition exists**:
  - `Ara-backend/api/services/app_secrets.py`
  - key validation, reserved key protection, normalization, merge precedence, fingerprinting.
- **Routes already exist**:
  - `GET/POST/PATCH/DELETE /apps/{app_id}/secrets` in `Ara-backend/api/routes/apps_sdk.py`.
- **Runtime injection exists**:
  - `Ara-backend/api/services/app_runtime.py`
  - resolves `runtime_profile.env`, `runtime_profile.secret_refs`, decrypts secret payloads, injects env into sandbox create call.
- **Runtime hash includes secret fingerprint**:
  - `AppRuntimeService._runtime_profile_hash(...)` + `secrets_runtime_fingerprint(...)` to trigger reprovision on secret rotation.
- **Test coverage exists in backend**:
  - `test_app_secrets.py`
  - `test_apps_sdk_secrets_routes.py`
  - plus related app runtime/access tests.

## Desired SDK End State

After this PR, public `ara-python-sdk` should support:

- `Secret.from_name(name, required_keys=None)`
- `Secret.from_dict(name, env_dict, required_keys=None)`
- `Secret.from_dotenv(name, filename=".env", required_keys=None)`
- `Secret.from_local_environ(name, env_keys=[...], required_keys=None)`
- `runtime(..., env={...}, secrets=[...])`
- deploy-time sync of local secret payloads to backend secrets routes
- manifest emission with:
  - `runtime_profile.env`
  - `runtime_profile.secret_refs`
  - no plaintext secret payload in app manifest
- no breaking changes to existing deploy/run/events/setup/invite/local flows.

## Non-Goals (This PR)

- No backend API contract changes.
- No migration changes.
- No frontend UI work.
- No new external secret manager providers.
- No non-Python SDK implementation.

## Implementation Plan

## Phase 0 - Contract Lock + Drift Audit (Day 0)

### Tasks

1. Confirm route and request/response contracts for:
   - `POST /apps/{app_id}/secrets`
   - `GET /apps/{app_id}/secrets`
2. Mirror backend validation constraints in SDK-side pre-validation:
   - env key format,
   - reserved key denylist and prefix rules,
   - secret name normalization.
3. Freeze naming and field shapes used by SDK:
   - `runtime_profile.env` (dict[str, str]),
   - `runtime_profile.secret_refs` (list[{name, required_keys?}]).

### Deliverable

- Shared internal mapping notes embedded as comments/docstrings in `core.py` and tests.

## Phase 1 - Public API Surface in SDK (Day 1)

### Files

- `src/ara_sdk/core.py`
- `src/ara_sdk/__init__.py`
- `tests/test_manifest.py` (expanded or split new files)

### Tasks

1. Add `SecretDefinition` + `Secret` helper API.
2. Add validation utilities:
   - secret name regex normalization,
   - env key validation,
   - reserved key checks.
3. Extend `runtime(...)` signature:
   - `env: Optional[dict[str, Any]] = None`
   - `secrets: Optional[list[Any]] = None`
4. Runtime serialization behavior:
   - include `env` in output as normalized strings,
   - include `secret_refs` only,
   - carry internal transient secret definitions for deploy-time sync only.
5. Export `Secret` in `__init__.py` and document in public API list.

### Acceptance Criteria

- `from ara_sdk import Secret` works.
- `runtime(env=..., secrets=...)` serializes to backend-compatible manifest fields.
- invalid keys/names fail early with actionable errors.

## Phase 2 - Deploy-Time Secret Sync (Day 1-2)

### Files

- `src/ara_sdk/core.py`

### Tasks

1. Add `_Http` client methods:
   - `upsert_secret(app_id, name, values)`
   - optional `list_secrets(app_id)` for diagnostics.
2. In `AraClient.deploy()`:
   - extract local secret definitions from runtime profile,
   - strip transient local secret payload fields before app upsert,
   - upsert app first (create or patch),
   - sync secret payloads via `/apps/{app_id}/secrets`,
   - only sync local-value secrets (`from_dict`, `from_dotenv`, `from_local_environ`),
   - do not mutate backend for `from_name` references.
3. Ensure warmup sequencing:
   - if `warm=True`, run warmup after secret sync, never before.
4. Return deploy result metadata:
   - synced secret names,
   - reference-only secret names.

### Acceptance Criteria

- Secret refs resolve at runtime immediately after deploy.
- No plaintext secret values ever sent in app manifest payload.
- `deploy --warm` works with secrets-first ordering.

## Phase 3 - CLI/DX Parity Improvements (Day 2)

### Files

- `src/ara_sdk/core.py`
- `README.md`

### Tasks

1. Add `up` alias -> deploy (if absent in public SDK path).
2. Ensure existing command matrix unchanged:
   - `deploy`, `run`, `events`, `setup`, `invite`, `local`.
3. Improve error clarity for:
   - missing local env vars in `from_local_environ`,
   - malformed dotenv for `from_dotenv`,
   - reserved key collisions.
4. Preserve safe default HTTP error behavior:
   - redacted by default,
   - debug body behind env flag only.

### Acceptance Criteria

- Commands remain backward compatible.
- New secrets path adds no regression in existing command flow.

## Phase 4 - Test Expansion (Day 2-3)

### Files

- `tests/test_manifest.py` (or split into focused test modules)

### New test groups

1. **Secret helper construction**
   - from_name/from_dict/from_dotenv/from_local_environ.
2. **Runtime serialization**
   - env normalization,
   - secret refs ordering and de-dup.
3. **Validation paths**
   - reserved keys/prefixes,
   - invalid names/keys,
   - missing dotenv/env keys.
4. **Deploy sync flow**
   - local secrets call upsert route,
   - from_name does not upsert,
   - warmup occurs after sync.
5. **Regression**
   - existing manifest/decorator tests still pass.

### Acceptance Criteria

- Robust unit coverage for new surface.
- Existing tests stay green.

## Phase 5 - Docs and Example Alignment (Day 3)

### Files

- `README.md`
- `examples/calcom-booking/README.md`

### Tasks

1. Add a secrets/runtime env section to root README:
   - quick examples for each `Secret` constructor.
2. Clarify deploy semantics:
   - local secret sync behavior,
   - reference-only behavior for `from_name`.
3. Keep provider-agnostic principle explicit.
4. Ensure docs avoid monorepo-only API references.

### Acceptance Criteria

- A new user can copy/paste docs and use secrets without reading monorepo code.

## Phase 6 - Release + PR Workflow (Day 3-4)

### Branch/PR

- Branch: `feat/sdk-secrets-runtime-env-parity`
- Worktree: `/Users/sve/ara-python-sdk-worktrees/sdk-secrets-runtime-env-parity`
- PR target: `origin/main`

### Tasks

1. Run test suite locally (`pytest`).
2. Add changelog/release note fragment in PR body.
3. Open PR with:
   - summary,
   - risk notes,
   - manual test steps.
4. Include explicit backend compatibility note:
   - requires backend with `/apps/{app_id}/secrets` support (already on Ara main).

### Acceptance Criteria

- PR is review-ready and includes end-to-end verification notes.

## Detailed Code-Level Checklist

1. `core.py`:
   - [ ] add regex/constants for secret/env validation
   - [ ] add `SecretDefinition` / `Secret`
   - [ ] extend `runtime()`
   - [ ] add secret definition extraction helper
   - [ ] add `_Http.upsert_secret()` + optional list helper
   - [ ] update `AraClient.deploy()` for sync sequencing
2. `__init__.py`:
   - [ ] export `Secret`
3. `README.md`:
   - [ ] add secrets usage and behavioral notes
4. tests:
   - [ ] helper constructor tests
   - [ ] runtime serialization tests
   - [ ] deploy sync and warmup ordering tests
   - [ ] regression tests for old paths

## Risk Register + Mitigations

1. **Risk: secret leakage in logs/errors**
   - Mitigation: never print values; keep redacted HTTP error default.
2. **Risk: deploy ordering bug (warmup before secrets)**
   - Mitigation: explicit sequencing test.
3. **Risk: breaking existing users**
   - Mitigation: additive API only, no command removal.
4. **Risk: backend validation mismatch**
   - Mitigation: mirror backend constraints in SDK tests and map errors clearly.

## Manual Verification Matrix

Use a real app script and validate:

1. `Secret.from_dict` + deploy:
   - secret appears in `GET /apps/{app_id}/secrets` metadata only
   - no plaintext in responses.
2. `Secret.from_name` + deploy:
   - no write call for that secret name,
   - runtime can use existing secret.
3. `runtime(env=...)`:
   - env propagates to runtime successfully.
4. `deploy --warm true`:
   - succeeds after secret sync.
5. `run/events/setup/invite/local`:
   - no regressions.

## Rollout Sequence

1. Merge SDK parity PR.
2. Release package version bump (`0.1.x` -> next patch/minor as needed).
3. Update docs references to use the released package behavior.
4. Follow with adapter/helper parity PR if any helpers remain monorepo-only.

## Definition of Done

Done means all are true:

- Public SDK supports `runtime(env=..., secrets=...)`.
- Deploy syncs local secrets via app secrets control-plane routes.
- No plaintext secret values in manifests or default errors.
- Tests cover new paths and existing flows remain green.
- Docs in `ara-python-sdk` reflect real shipped behavior.
