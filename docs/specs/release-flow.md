# Spec: Release flow (one release and deploy pipeline for the whole skill-platform)

> Status: Approved (2026-09-20), not yet implemented | Platform arc (skillforge first, then skillsite and skillbot)
> This spec is also the decision record (no separate ADR: *Decided defaults*, *Verified behavior* and
> *Trade-offs accepted* carry the why). Written in skillforge because it is the first adopter; the **platform contract** below is what the other
> repos copy. Every GitHub behavior this spec relies on was verified in a throwaway repo (see
> *Verified behavior*); nothing here assumes a feature GitHub does not have.

## Problem statement

The three deployable repos release in three different ways:

| Repo | Release trigger | Version bump | Deploy |
|---|---|---|---|
| skillforge | version in `pyproject.toml` has no GitHub release yet, checked after CI on `main` ([`release.yml`](../../.github/workflows/release.yml)) | manual workflow opens a bump PR ([`version-bump.yml`](../../.github/workflows/version-bump.yml)) | deploy webhook, image `:latest` |
| skillsite | manual `workflow_dispatch` | a bot commits and tags directly on `main` | deploy webhook |
| skillbot | push to `main` -> dev, tag `v*` -> prod | none (tag by hand) | two deploy webhooks, unpinned actions |

Three mental models for one person is friction on every release. On top of that:

- **The deploy is fire-and-forget.** The webhook call succeeding only means Dokploy accepted the request. A
  failed migration or a crash-looping container leaves the workflow green.
- **`:latest` with `pull_policy: always`** ([`compose.yml`](../../compose.yml)) means any restart of the stack
  may pull a different version than the one that was released.
- **Nothing forces CI to be green on `main`.** The `main` ruleset requires signatures and linear history, but
  no status check.
- **`codeql.yml` produces alerts nobody reads**, and there are no local hooks, so formatting and commit-message
  slips are only caught in CI.

## Goals

1. **One flow, three repos.** Same workflow file names, same job names, same secrets, same `just` entry points.
   Knowing how one repo releases means knowing all of them.
2. **Releasing is one deliberate act:** shipping the release PR. Version, changelog, tag, image, publish and
   deploy follow from it without further manual steps.
3. **A readable `main`.** One commit per logical slice, conventional commit messages, one release commit per
   version. Tags on `main` plus `CHANGELOG.md` are the condensed view - no release branch.
4. **The deploy reports the truth.** The workflow talks to the Dokploy API, waits for the deployment to finish
   and verifies the running version. A failed deploy is a red workflow.
5. **`main` only accepts green commits**, without giving up `git ship` and Leon's signature on feature commits.
6. **Less machinery than today**, not more. Everything the platform does not need is removed.

## Non-goals

- **Release branches, backports, maintenance lines.** One prod, one supported version.
- **A dev or staging environment.** Only prod exists platform-wide. Nothing deploys on a plain push to `main`;
  skillbot's dev deploy is dropped (the bot runs locally via `just start`).
- **Supply-chain hardening from `github-actions-playground`:** digest pinning, attestations, signed release
  tags, image promotion, release-overlap verification, multi-arch images. Deliberately out.
- **Automatic rollback.** An app rollback does not roll back an Alembic migration; pretending otherwise is
  worse than fixing forward. Rollback stays a documented manual procedure.
- **A CodeQL workflow.** GitHub's code scanning *default setup* can be switched on in the repo settings later
  without any file in the repo.
- **Central reusable workflows** in a shared repo. Considered for later (P2) once the flow is stable in two repos.
- **skillcore.** A private library, not deployed; it may adopt the release half later (note: on the Free plan
  org secrets and rulesets do not apply to private repos).

## Decided defaults

| Topic | Decision | Rationale |
|---|---|---|
| **A - Release mechanism** | [release-please](https://github.com/googleapis/release-please) (`release-please-action`, pinned by SHA). It keeps one *release PR* up to date (version bump + `CHANGELOG.md`); shipping that PR is the release. | Same tool for Python and Node; version derived from conventional commits; replaces the hand-written bump workflow. Verified with `git ship` and the rulesets. |
| **B - Environments** | Prod only. One GitHub Environment `production` per repo. | Decided 2026-09-19. |
| **C - Branching** | Short-lived feature branches -> PR -> `git ship` (fast-forward) onto `main`. No release branch. Tags `vX.Y.Z` on `main`. | Linear history and signatures already enforced; a second branch would be a second history to keep in sync. |
| **D - History on `main`** | One PR per slice (not per spec); before shipping, fold fixups and "tick the spec" commits into the slice they belong to. Every commit on `main` is green on its own and conventional. | ~6-8 commits for an arc like `crm-api.md` instead of ~40 - but not 1-2, which would destroy `git bisect` and the story. |
| **E - Release PR author** | An org-wide **GitHub App**; release-please runs with its installation token. The App is **`skill-platform-release`** - the installed `skillsite-release-bot`, renamed (no `bot` in the name: GitHub appends `[bot]`, and it keeps clear of *skillbot*) and extended to the other repos. One App per role; it does releases only. | PRs opened with `GITHUB_TOKEN` get no CI run, so the required check (G) would reject the release PR. Verified; see below. |
| **F - Signatures** | Feature commits carry Leon's signature (via `git ship`). The one release commit per version is created through the GitHub API and carries GitHub's signature ("Verified"). Tags are lightweight and unsigned. | Satisfies the `required_signatures` rule; same trust level as today's bump PR. |
| **G - Gate on `main`** | The `main` ruleset additionally requires the status check **`check`**. Every repo's CI exposes a job with exactly this name. | A fast-forward keeps the commit SHA, so the green check from the PR still counts on push. Verified. |
| **H - Deploy transport** | Dokploy **API** (`x-api-key`): `compose.deploy`, then poll `deployment.allByCompose` until `done` / `error`, then verify the health endpoint. The deploy webhook is removed. | Authenticated, observable, fails loudly. Endpoints proven in `github-actions-playground`. |
| **I - Deployed version** | P0 keeps `:latest`; P1 pins `image: ...:vX.Y.Z` in `compose.yml`, rewritten by release-please in the release commit. | One annotated line; `main` then records what prod runs. |
| **J - Health contract** | Every service with a public HTTP endpoint answers `GET /health` with at least `status` and `version`. Services without HTTP (skillbot) are verified by the Dokploy deployment status alone. | Lets the deploy job prove that the *new* version is the one answering. |
| **K - Local hooks** | [lefthook](https://github.com/evilmartians/lefthook) with one `lefthook.yml` that only calls `just` recipes; identical across repos. Hooks are a convenience, CI stays the gate. | Language-agnostic (Python and Node repos), single binary, no per-language hook framework. |
| **L - Pre-1.0 versioning** | `bump-minor-pre-major: true`: while `0.x`, a breaking change bumps the minor, `feat` bumps the minor, `fix` the patch. | Nothing is live yet; `1.0.0` should be a deliberate decision, not a side effect of one `!`. |

## Verified behavior

Probed on 2026-09-20 in a throwaway repo with the same rulesets as skillforge (`required_signatures`,
`required_linear_history`, no force-push, protected `v*` tags), `release-please-action` v5.0.0, three complete
release cycles shipped with the real `git ship` alias.

| Question | Result |
|---|---|
| Does release-please recognise a release PR merged by fast-forward push? | **Yes.** GitHub marks the PR `MERGED` (merge commit = PR head), the label flips from `autorelease: pending` to `autorelease: tagged`, tag and GitHub release are created. skillforge PRs #112/#113 show the same `MERGED` state. |
| Do the `release_created` / `tag_name` / `sha` outputs drive follow-up jobs in the same workflow? | **Yes.** This is how build and deploy are chained - events created with `GITHUB_TOKEN` never start *other* workflows, so an `on: release` trigger is not an option. |
| Does the release commit pass `required_signatures` + `required_linear_history`? | **Yes.** Commits created through the GitHub API are signed by GitHub (`web-flow`). Observed for `GITHUB_TOKEN`; to be re-confirmed for the App token in P0-1. |
| Can `uv.lock` and `openapi.json` follow the version? | **Yes**, via `extra-files`: a `toml` updater with `$.package[?(@.name.value=='<package>')].version` and a `json` updater with `$.info.version`. `uv lock --check` stays consistent. A JSONPath that stops matching is a **silent no-op** - CI's `uv sync --locked` is the backstop. |
| Tag format? | `include-component-in-tag: false` yields plain `vX.Y.Z`, matching the existing tags. |
| Required status check + `git ship` for a normal PR? | **Works** - same SHA, the green check is already there. |
| ... and for the release PR opened with `GITHUB_TOKEN`? | **Rejected:** `Required status check "check" is expected`. The PR's CI run is created but never runs jobs. |
| Workarounds without an App? | Closing and reopening the PR as a user starts CI, then shipping works (tested). Starting CI on the release branch via `workflow_dispatch` produced a green check that did **not** satisfy the rule (tested). Hence decision E. |
| What if a step after the action fails in the release-please job? | The release already exists but build and deploy are skipped - a half-done release. Hence P0-3's "nothing after the action" rule and the manual deploy entry point. |

## Trade-offs accepted

- **Commit messages become load-bearing.** The changelog and the version bump are derived from them; a sloppy
  message on `main` is a wrong changelog line or a missed bump. P1-2's `commit-msg` hook exists for this.
- **The release workflow holds a Dokploy API key**, which can do more than a single-purpose webhook URL. It
  lives only in the `production` environment, restricted to `main`.
- **One commit per release is signed by GitHub, not by Leon.** It is mechanical, reviewable as a PR, and the
  same trust level today's bump PR has.
- **The platform depends on one GitHub App and its private key.** If the App is unavailable, the fallback is
  to close and reopen the release PR as a user so CI runs on it.

## Target shape

```
feature branch -> PR (CI: job "check" green) -> git ship -> main
                                                            |
                          release.yml: release-please (App token) keeps the release PR current
                                                            |
                         you ship the release PR (CI green on it) = the release
                                                            |
        release.yml on that push: tag vX.Y.Z + GitHub release + CHANGELOG.md   (release_created == true)
                                                            |
               build image (:vX.Y.Z, :sha-..., :latest)  ->  repo-specific publish (forge: PyPI client)
                                                            |
        deploy.yml: Dokploy API compose.deploy -> wait for done/error -> GET /health (status + version)
```

## Platform contract

What is identical in every repo; everything else is repo-specific detail behind these names.

- **Workflows:** `ci.yml` (PRs and pushes to `main`; contains the job **`check`**), `release.yml` (push to
  `main`: release-please, then build / publish / deploy when `release_created`), `deploy.yml` (`workflow_call`
  + `workflow_dispatch`; the only place that talks to Dokploy).
- **Config:** `release-please-config.json` and `.release-please-manifest.json` in the repo root; tags `vX.Y.Z`.
- **`just` entry points:** `just check` (everything that must be green before a push), `just pre-commit` (the
  fast subset for the commit hook). CI's `check` job runs at least `just check`.
- **Org-level:** variable `RELEASE_APP_CLIENT_ID`, secret `RELEASE_APP_PRIVATE_KEY` (the names skillsite already
  uses), variable `DOKPLOY_BASE_URL`.
- **Environment `production` (per repo):** secret `DOKPLOY_API_KEY`, variables `DOKPLOY_COMPOSE_ID` and
  `HEALTH_URL` (empty for services without HTTP). Deployment branches restricted to `main`. No required
  reviewer - shipping the release PR already is the approval.
- **Rulesets:** `main` - no deletion, no force-push, linear history, signatures, required check `check`.
  Tags `v*` - no deletion, no update.
- **Conventions:** conventional commits; all actions pinned by SHA and kept current by Dependabot (grouped).

## Requirements

### Must-have (P0) - skillforge

**P0-1 - release-please with the org App.**
- *Technique:* rename `skillsite-release-bot` to `skill-platform-release` and extend its installation to
  skillforge (permissions: contents, pull requests, issues - read and write). Client ID and private key
  survive the rename; skillsite's current `release.yml` only carries the old name as a `git config` display
  string until it adopts this flow. Then move its credentials to the org-level variable/secret named in the
  contract. New `release.yml`: job `release-please` mints a token with `actions/create-github-app-token` and
  passes it to `release-please-action`; **no step follows the action in this job**. Outputs: `release_created`,
  `tag_name`, `version`, `sha`.
- *Technique:* `release-please-config.json` with `release-type: python`, `include-component-in-tag: false`,
  `bump-minor-pre-major: true`, `changelog-sections` that show only `feat`, `fix`, `perf` and `revert` (a dry
  run on today's history listed 34 documentation entries, mostly spec ticks, next to 26 features and 12
  fixes) and `extra-files` for `uv.lock` (package `skillforge`) and `openapi.json`;
  `.release-please-manifest.json` starts at the current version (`0.3.0`, tag `v0.3.0` exists).
- *Acceptance criteria:*
  - [ ] After a `feat`/`fix` commit lands on `main`, a release PR exists whose diff touches exactly
        `pyproject.toml`, `uv.lock`, `openapi.json`, `CHANGELOG.md` and the manifest.
  - [ ] The release PR's head commit is shown as *Verified* and its CI run (job `check`) executes and is green.
  - [ ] `just openapi-check` and `uv sync --locked` pass on the release PR (proves both `extra-files` paths match).
  - [ ] `git ship` of the release PR creates tag `vX.Y.Z` and a GitHub release; the PR label becomes
        `autorelease: tagged`.

**P0-2 - CI is a required check.**
- *Technique:* add `required_status_checks` with context `check` to the `main` ruleset (after P0-1, otherwise
  the release PR cannot be shipped). The job in [`ci.yml`](../../.github/workflows/ci.yml) is already named `check`.
- *Acceptance criteria:*
  - [ ] Pushing a commit without a green `check` to `main` is rejected.
  - [ ] `git ship` of a green PR and of a green release PR both succeed.

**P0-3 - Build and publish hang off `release_created`.**
- *Technique:* in `release.yml`, the existing image job ([`build.yml`](../../.github/workflows/build.yml), tags
  `:vX.Y.Z`, `:sha-...`, `:latest`, `linux/amd64`) and the PyPI client jobs run with
  `if: needs.release-please.outputs.release_created == 'true'` and build from the released `sha`. The
  GitHub release is created by release-please; the separate `gh release create` job goes away. Image and
  client coordinates move from the release notes into the README.
- *Acceptance criteria:*
  - [ ] A push to `main` that is not a release runs release-please only; build, publish and deploy are skipped.
  - [ ] A release produces the image `ghcr.io/nachhilfe-leon-weimann/skillforge:vX.Y.Z` and
        `skillforge-client==X.Y.Z` on PyPI, both built from the tagged commit.

**P0-4 - Deploy through the Dokploy API, verified.**
- *Technique:* `deploy.yml` (environment `production`, concurrency group `deploy-production`, never cancelled)
  runs one script: abort if the latest deployment is `running`; `POST compose.deploy` with a title and a
  description carrying version, SHA and run id; poll `deployment.allByCompose` until that deployment is
  `done` (continue) or `error` / `cancelled` / timeout (fail); then poll `HEALTH_URL` until `status` is healthy
  and `version` equals the released version, or time out (fail). No rollback. `workflow_dispatch` input:
  the version to expect - this is the manual re-run path.
- *Technique:* `SystemHealthCheckResponse` in [`schemas.py`](../../app/services/system/schemas.py) gains
  `version`, filled from `get_project_version()`; `just openapi` afterwards (decision J).
- *Technique:* remove the secret `DEPLOY_WEBHOOK_URL`, rotate the webhook token in Dokploy so the old URL is
  dead, and make sure Dokploy's own auto-deploy on push is off for the service.
- *Acceptance criteria:*
  - [ ] A successful release ends with a green `deploy` job whose summary names the Dokploy deployment and
        the verified version.
  - [ ] A deployment that ends in `error` (e.g. failing `migrate` service) turns the workflow red.
  - [ ] A healthy container that reports the *old* version turns the workflow red after the timeout.
  - [ ] `deploy.yml` can be dispatched by hand for the current release and passes.

**P0-5 - Remove what the new flow replaces.**
- *Technique:* delete `version-bump.yml`, `codeql.yml` and `.github/codeql/`; drop the `check-release` logic
  and the webhook job from `release.yml`; remove the `just` recipes `create-version-bump`, `version-info`,
  `release-version`, `bump-version` and `scripts/version.py` if nothing else uses them. Switch on secret
  scanning and push protection in the repo settings (free for public repos).
- *Acceptance criteria:*
  - [ ] `.github/workflows/` contains `ci.yml`, `build.yml`, `release.yml`, `deploy.yml` - nothing else.
  - [ ] `CLAUDE.md`, `README.md` and [`ARCHITECTURE.md`](../ARCHITECTURE.md) describe the new flow and
        point to this spec for the why.

### Nice-to-have (P1)

**P1-1 - Pin the deployed version.** `compose.yml` references `...:vX.Y.Z` with an
`x-release-please-version` annotation on each `image:` line and a `generic` `extra-files` entry; `:latest`
stays as a convenience tag only. Rollback procedure documented: set the previous tag in a PR, ship it,
dispatch `deploy.yml`. *Criterion:* after a release, `compose.yml` on `main` names the released version and
prod runs exactly that image.

**P1-2 - Local hooks.** `lefthook.yml`: `pre-commit` -> `just pre-commit` (format check + lint, staged files
where the tool allows), `commit-msg` -> conventional-commit pattern check, `pre-push` -> `just check`. Installed
via `lefthook install`; documented in the README. *Criterion:* a non-conventional message is rejected locally;
`--no-verify` still works (CI is the gate).

**P1-3 - Deploy notification.** One Discord message per deploy result (skillbot has this today), driven by a
shared webhook secret. *Criterion:* success and failure both notify, with version and run link.

**P1-4 - History convention in `CLAUDE.md`.** Decision D as a short rule set, including "one PR per slice" and
"fold `docs(specs): tick` commits into the slice".

### Future considerations (P2)

- **Shared workflows:** move `deploy.yml` and the deploy script into a public `platform-workflows` repo and
  call them with `uses: ...@vN` (a public repo cannot call workflows from a private one).
- **Code scanning default setup**, if alerts start being read.
- **A required reviewer on `production`**, if someone other than Leon ever ships.

## Adoption in the other repos

Order: skillforge (this spec) -> skillsite -> skillbot. Copy first, extract shared pieces on the third (P2).

- **skillsite:** `release-type: node` on the root `package.json`; replaces its manual `release.yml` (bot commit
  + tag). Already has the App. Needs the `/health` `version` field and a job named `check`.
- **skillbot:** `release-type: python`; replaces `build-deploy.yml`; the dev deploy and both webhooks go away;
  actions get pinned. No HTTP health endpoint -> deployment status only.

## Open questions

- **skillbot's Dokploy target.** The repo has no `compose.yml`; if the service is a Dokploy *application*
  rather than a *compose* service, either move it to a repo `compose.yml` (preferred, uniform) or let the
  deploy script support `application.deploy`. Decide at adoption.
- **skillbot's two lockfiles** (`uv.lock`, `uv.lock.prod`): both contain the package version; both need an
  `extra-files` entry or the split should be revisited. Decide at adoption.
- **Dokploy API key scope.** The key acts as the user who created it. If the instance allows a restricted
  user, create one for deployments; otherwise accept the broader key as an environment secret.
- **First release PR.** It will collect everything since `v0.3.0` (CRM P0 + P1) into `0.4.0`. Fine as is, or
  trim the changelog by hand in the release PR before shipping?

## Success metrics

- *Manual steps per release:* **1** (ship the release PR). Today: 3 (dispatch bump, merge bump PR, watch webhook).
- *Flows to remember:* **1** for three repos. Today: 3.
- *Silent deploy failures:* **0** - every failed deployment or version mismatch is a red workflow.
- *Workflow files in skillforge:* 4 (today 5), with roughly half the YAML.

## Timeline / phasing

One PR per requirement, `just check` green on each:

1. **`/health` reports `version`** (the app half of P0-4) - independent, and the first new-flow deploy needs it.
2. **P0-1 + P0-3 together** - the new `release.yml` replaces the old one in a single PR, keeping the webhook
   deploy job for now. They cannot be staged: the old version-driven check would find the GitHub release that
   release-please created seconds earlier and skip build and deploy.
3. **P0-2** - required check (settings only, no PR), once a release PR from the App has shown a green `check`.
4. **P0-4** - deploy script and `deploy.yml`; `release.yml` switches from the webhook to it.
5. **P0-5** - cleanup and docs. Then ship the release PR: the first real release through the new flow.
6. **P1-1 .. P1-4** as independent follow-ups; then skillsite, then skillbot.

**Dependency:** Leon extends the App installation and creates the org variable/secret, the `production`
environment and the Dokploy API key - these cannot be done from a PR.

## Rules for implementing agents

- Follow [`CLAUDE.md`](../../CLAUDE.md): English only, symbol references instead of line numbers, never
  hand-edit `openapi.json`, `just check` green before every commit, conventional commits.
- Pin every action by full SHA with a version comment, as the existing workflows do.
- Never print or echo secrets; pass the Dokploy API key through a curl config file, not the command line.
- Do not add rollback, digest pinning or attestation logic - they are non-goals, not omissions.
- Repo settings, rulesets, the App installation and Dokploy configuration are changed by Leon, not by an
  agent; describe the exact setting in the PR instead.
- Tick the acceptance checkboxes in this file in the PR that fulfils them and flip the status line when P0 is done.
