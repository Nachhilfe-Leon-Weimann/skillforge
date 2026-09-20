# Spec: Project intake (issues and PRs land on the board, with their module, assigned to their author)

> Status: In progress - P0-0 done, P0-1 implemented (live verification follows its merge), P0-2 and P0-3 open.
> Tracking: [#127](https://github.com/Nachhilfe-Leon-Weimann/skillforge/issues/127)
> Platform arc (skillforge first, then skillsite and skillbot), same shape as
> [`release-flow.md`](release-flow.md): written here because skillforge is the first adopter; the **platform
> contract** below is what the other repos copy.
> This spec is also the decision record (no separate ADR).

## Problem statement

Planning happens on one org-wide board, the
[skill-platform project](https://github.com/orgs/Nachhilfe-Leon-Weimann/projects/2). Three things are still done
by hand on every issue and every PR, in every repo:

- **Putting it on the board.** GitHub's built-in *Auto-add to project* workflow covers one repository per
  workflow, and the number of such workflows depends on the plan of the project's **owner**: one on Free. The
  project belongs to the org, the org is on Free, and that one workflow is already in use. Leon's personal Pro
  plan does not count.
- **Assigning it.** An item without an assignee is invisible in every "assigned to me" view. With one developer
  the answer is almost always "whoever opened it".
- **Setting its Module.** The board is sliced by the single-select field *Module* (`forge`, `bot`, `site`,
  `core`). The value follows from the repo, yet it is picked by hand - and an item without it drops out of
  every per-module view.

An item that is forgotten is simply missing from the board, and nothing notices.

## Goals

1. **Every issue and every PR lands on the board** without a manual step, in all three platform repos.
2. **An item nobody is assigned to gets its author as assignee.** An assignee set on purpose is never touched.
3. **Every item has its Module.** The repo's module is set on intake; a Module set on purpose is never touched.
   Which module a repo stands for is one line of configuration.
4. **One flow, three repos.** Same file name, same job names, same org variable and secret - the platform
   contract of `release-flow.md` applies.
5. **No new credential.** The org App that already opens the release PRs does this too.
6. **It never gets in the way.** The workflow is not a required check, and an item it cannot handle (an author
   who cannot be assigned, a bot) leaves a green run, not a red one.

## Non-goals

- **Setting the other project fields** (Status, Priority, Effort, Iteration). The project's built-in *Item added
  to project* workflow already sets the status; the rest is planning, not intake. Module is the exception
  because it is not a judgement: it follows from the repo.
- **Labels, reviewers, milestones, issue types.**
- **Assigning bot-authored PRs.** A bot cannot be an assignee; the release PR and Dependabot PRs stay unassigned.
- **A GitHub plan upgrade, or a personal access token.** A PAT is tied to Leon's account and expires.
- **A third-party action for the assignment.** It is one REST call.
- **A shared reusable workflow.** Same stance as `release-flow.md`: copy first, extract on the third repo (its P2,
  a public `platform-workflows` repo). This workflow joins that extraction, it does not start it.
- **skillcore and the `student-*` repos.** skillcore is private: on the Free plan org variables and secrets do
  not reach private repos, and the App is installed on selected repos only. It may adopt this later with
  repo-level copies of the variable and the secret.

## Decided defaults

| Topic | Decision | Rationale |
|---|---|---|
| **A - Mechanism** | One workflow per repo, **`triage.yml`**, with two independent jobs: **`add-to-project`** (which also sets the Module, decision J) and **`assign-author`**. | Independent on purpose: a broken App token must not stop the assignment, and the other way round. |
| **B - Adding** | [`actions/add-to-project`](https://github.com/actions/add-to-project) (GitHub's own action, pinned by SHA), pointed at the project URL. | Maintained by GitHub, one input besides the token. Adding an item that is already on the board is a no-op, so it can run next to the built-in workflow. |
| **C - Token for the board** | An installation token of the org App **`skill-platform-release`**, minted with `actions/create-github-app-token` and narrowed to `permission-organization-projects: write` plus read on issues and pull requests (the action resolves the item it adds). Credentials are the existing org variable `RELEASE_APP_CLIENT_ID` and org secret `RELEASE_APP_PRIVATE_KEY`. | `GITHUB_TOKEN` cannot reach an org project at all. The App, its key and both names already exist in every platform repo. This widens the App's role - see *Trade-offs accepted*; decision E in `release-flow.md` is amended accordingly. |
| **D - Token for the assignment** | The workflow's own `GITHUB_TOKEN` with `issues: write` and `pull-requests: write` on that job only; top-level `permissions: {}`. | Assigning is a repo-level write; it needs neither the App nor a secret. |
| **E - Triggers** | `issues` and `pull_request_target`, types `opened` and `reopened`. | `pull_request_target` runs the workflow from `main` and has the secret even for a PR from a fork (the repos are public). An issue transferred into a repo arrives as `opened`. `reopened` is the cheap second chance for an item whose first run failed. |
| **F - What is added** | Everything, including bot-authored PRs (release PR, Dependabot). | That is what the board holds today: release PR #117 and five Dependabot PRs are on it. Merged items move to *Done* and are auto-archived by the project's built-in workflows, so they do not pile up. |
| **G - Who is assigned** | The author, if and only if the item has **no assignee at the moment the job runs** and the author is a user (not a bot) who can be assigned in that repo. Anything else: do nothing, stay green. | "Nobody assigned" is read from the API at run time, not from the event payload, so an assignee picked while creating the item or seconds after it always wins. Assignability is checked first (`GET /repos/{owner}/{repo}/assignees/{login}`), so an outside contributor does not produce a failed run. |
| **H - Built-in auto-add** | Switched **off** once `triage.yml` is proven in the repo it covers. *Auto-add sub-issues to project* stays on. | One mechanism per concern; a second, plan-limited one that covers a single repo is a trap for the next person who wonders why one repo behaves differently. |
| **I - Not a gate** | `triage.yml` is never added to the required checks of the `main` ruleset. | A GitHub API hiccup must not block a merge. A red run is visible in the Actions tab and costs one click on the board. |
| **J - Module** | The job that adds the item sets the project's single-select field **Module** to the repo's module, **only when the field is empty**. The module is the workflow-level `env` entry `MODULE` (skillforge: `forge`) and is given by option *name*; project, field and option ids are looked up at run time from the item the action returns. A name the project does not know fails the run. | Same rule as for the assignee: a value chosen by hand wins, e.g. a forge issue that really belongs to `core`. A name in the file is reviewable and survives a recreated field; an opaque option id is neither. A misconfigured name must be loud - it would otherwise leave every item without a Module. In the file rather than a repo variable: versioned, visible in the diff of P0-2, and it maps onto an input of the shared workflow later (P2). |

## Platform contract

Identical in every adopting repo:

- **Workflow:** `.github/workflows/triage.yml`, jobs `add-to-project` and `assign-author`, top-level
  `permissions: {}`, every action pinned by full SHA with a version comment (Dependabot keeps them current).
- **Configuration:** two workflow-level `env` entries. `PROJECT_URL`
  (`https://github.com/orgs/Nachhilfe-Leon-Weimann/projects/2`) is the same everywhere; **`MODULE` is the one
  repo-specific line** of the file: `forge`, `site`, `bot`.
- **Org-level (existing):** variable `RELEASE_APP_CLIENT_ID`, secret `RELEASE_APP_PRIVATE_KEY`.
- **App `skill-platform-release`:** installed on the repo, with the organization permission *Projects: read and
  write* accepted on the org installation.
- **`pull_request_target` hygiene:** the workflow never checks out code and never interpolates event data into
  a script. Values from the event (number, author login) reach `run:` steps through `env:` only. Each repo
  guards this with a test next to its other config tests (skillforge:
  [`test_triage_workflow.py`](../../tests/test_triage_workflow.py)).

## Verified behavior

Checked on 2026-09-20 against the live org (read-only API calls).

| Question | Result |
|---|---|
| Which plan counts for the built-in auto-add limit? | The org is on **Free** (`GET /orgs/...`: `plan.name = free`). Project #2 has exactly one *Auto-add to project* workflow, enabled - the Free allowance. |
| Does the App have the project permission? | **Yes, since the evening of 2026-09-20** - and it took two steps. Declaring `organization_projects: write` on the App (`GET /apps/skill-platform-release`) changed nothing for the org: the installation kept listing only `contents`, `issues`, `pull_requests` (write) and `metadata` until the new permission was **accepted on the installation** (org settings -> GitHub Apps -> `skill-platform-release` -> review request). `GET /orgs/.../installations` is the check that counts. |
| Do the credentials reach the repos? | `RELEASE_APP_CLIENT_ID` and `RELEASE_APP_PRIVATE_KEY` are org-level with visibility `all`. On Free that means all **public** repos: skillforge, skillsite, skillbot. |
| What is on the board today? | 80 items from skillforge, skillsite, skillbot and skillcore, among them Dependabot PRs and the release PR. Hence decision F. |
| Does the Module logic work against the real project? | **Yes.** The lookup query and the mutation of `triage.yml` were run verbatim (with a user token) against the item of #127: the field was empty, `forge` resolved to its option id, an unknown name resolved to nothing (the red branch), the mutation set the field, and the read-back returned `forge` (the "already set" branch). Project, field and option ids all come out of the one lookup by item id. |
| Do the assignment calls behave as decision G assumes? | **Yes.** `GET .../issues/{number}` answers for a PR number too; `GET .../assignees/{login}` exits 0 for Leon and fails with `HTTP 404` for an outside account - the text the script matches on. |
| Can the workflow be tried before it is on `main`? | **No.** `issues` and `pull_request_target` run the workflow file of the default branch, so the PR that adds `triage.yml` does not trigger it. The first issue or PR opened after the merge is the live test. |
| Action versions | `actions/add-to-project` v2.0.0 (`5afcf98fcd03f1c2f92c3c83f58ae24323cc57fd`), `actions/create-github-app-token` v3.2.0 (`bcd2ba49218906704ab6c1aa796996da409d3eb1`, the pin `release.yml` already uses). |

**Not verified yet - the first items after the merge of P0-1 prove or refute them:**

- An installation token that is scoped to one repository and narrowed to `organization-projects: write` can add
  an item to the org project and write its Module. (The action's README only documents personal access
  tokens; the local run above used a user token.) If it fails, mint the token with `owner:` set instead of the
  repo default.
- Whether a `pull_request_target` run **triggered by Dependabot** receives the org Actions secret. GitHub treats
  Dependabot runs like fork runs for `pull_request` (no Actions secrets, only Dependabot secrets); its docs are
  not explicit for `pull_request_target`. See *Open questions*.

## Requirements

### Must-have (P0)

**P0-0 - Accept the App permission (Leon, settings only).**
- *Technique:* accept *Projects: read and write* on the org installation of `skill-platform-release`.
- *Acceptance criteria:*
  - [x] `GET /orgs/Nachhilfe-Leon-Weimann/installations` lists `organization_projects: write` for the App.
        *(accepted 2026-09-20)*

**P0-1 - `triage.yml` in skillforge.**
- *Technique:* the workflow described by decisions A-G and J. `add-to-project`: mint the App token, run the
  action, then set the Module: one GraphQL lookup by the returned item id (current value, project id, field id,
  options), stop if a value is set, fail if `MODULE` names no option, else `updateProjectV2ItemFieldValue`.
  `assign-author`: skip when the author's `type` is `Bot`; otherwise read the item through
  `GET /repos/{owner}/{repo}/issues/{number}` (PRs are issues for this endpoint), stop if `assignees` is not
  empty, stop if the author is not assignable, else `POST .../issues/{number}/assignees`. `gh api` with
  `GH_TOKEN: ${{ github.token }}` is enough; no checkout, no script file.
- *Technique:* [`test_triage_workflow.py`](../../tests/test_triage_workflow.py) guards the hygiene rules of the
  platform contract as part of `just check`: no checkout, no `${{ }}` inside a `run:` block, top-level
  `permissions: {}`, every action pinned, exactly one `MODULE` line.
- *Technique:* docs in the same PR - the `.github/` line of the layout block in [`CLAUDE.md`](../../CLAUDE.md)
  and a pointer from the *Platform contract* of `release-flow.md` to this spec.
- *Acceptance criteria:*
  - [x] `actionlint` (with `shellcheck`) passes on `triage.yml`; the hygiene tests are part of `just check`.
  - [x] The token step requests `organization-projects: write`, `issues: read` and `pull-requests: read` and
        nothing else; the workflow has top-level `permissions: {}` and no checkout step. *(enforced by the tests)*
  - [x] The Module and assignment calls do what decisions G and J say. *(run by hand against the live project,
        see Verified behavior; #127 got its `forge` that way)*

  Live, after the merge (the workflow cannot run earlier - see *Verified behavior*):
  - [ ] A new issue without an assignee is on the board, Module `forge`, assigned to its author within a minute.
  - [ ] A new PR from a branch of the repo: same.
  - [ ] An item created **with** an assignee keeps exactly that assignee; an item whose Module was changed by
        hand keeps it after a close and reopen.
  - [ ] The release PR (author `skill-platform-release[bot]`) is on the board with its Module, unassigned, and
        both jobs are green.
  - [ ] An item that is already on the board (added by the built-in workflow) leaves a green run and no duplicate.

**P0-2 - Roll out to skillsite and skillbot.**
- *Technique:* copy `triage.yml` and its test, change the one `MODULE` line (`site`, `bot`). One PR per repo,
  tracked by an issue in that repo.
- *Acceptance criteria:*
  - [ ] The first two live criteria of P0-1 hold in skillsite (Module `site`) and in skillbot (Module `bot`).
  - [ ] `diff` between the three `triage.yml` files shows the `MODULE` line and nothing else.

**P0-3 - Retire the built-in auto-add (Leon, settings only).**
- *Technique:* in the project's workflow settings, switch off *Auto-add to project* (decision H). Only after
  P0-1 has been green for the repo that workflow covers.
- *Acceptance criteria:*
  - [ ] The project lists *Auto-add to project* as disabled; a new issue in each of the three repos still lands
        on the board.

### Nice-to-have (P1)

**P1-1 - One-off backfill.** Open issues and PRs that never made it onto the board are added once with
`gh project item-add 2 --owner Nachhilfe-Leon-Weimann --url <url>` over `gh issue list` / `gh pr list` of the three
repos. A command in the PR description, not a workflow. *Criterion:* no open issue or PR of the three repos is
missing from the board.

### Future considerations (P2)

- **Shared workflow:** `triage.yml` moves into the public `platform-workflows` repo together with `deploy.yml`
  (`release-flow.md`, P2) and the repos keep a three-line caller.
- **skillcore:** adopt with repo-level variable and secret, once the App is installed there.

## Trade-offs accepted

- **The release App is no longer single-purpose.** `release-flow.md` decided "one App per role; it does releases
  only". A second App would mean a second private key, a second pair of org credentials and a second
  installation to keep in sync, for a workflow that needs one org permission. The role is widened to *platform
  repo automation* instead, and every workflow narrows its token to the permissions it needs
  (`permission-*` inputs), so the intake token cannot write contents and the release token cannot write projects.
- **The App's private key is now used by a workflow that outsiders can trigger** (anyone can open an issue on a
  public repo). `issues` and `pull_request_target` runs execute the workflow file from `main`, the workflow
  checks out nothing and passes no event data into a shell. The key is read by one pinned first-party action.
  This is the reason for the hygiene rule in the platform contract - it is not optional.
- **Three copies of one file.** Accepted until the P2 extraction; P0-2's one-line `diff` keeps them honest.
- **A failed intake stops nothing.** Not being a required check (decision I) means a red `triage` run is only
  seen by someone who looks. The cost of a miss is one manual click; the cost of a gate would be blocked merges.

## Open questions

- **Dependabot PRs and the secret.** If a Dependabot-triggered `pull_request_target` run does not get
  `RELEASE_APP_PRIVATE_KEY`, `add-to-project` fails on every Dependabot PR. Two ways out: mirror the key as an
  org **Dependabot** secret, or skip `add-to-project` for `dependabot[bot]` and drop Dependabot PRs from
  decision F. Decide on the first Dependabot PR after P0-1; prefer skipping - a second copy of the key is worse
  than a bump PR that is not on the board.

## Timeline / phasing

1. **P0-0** - Leon accepts the permission. Blocks everything else. *Done 2026-09-20.*
2. **P0-1** - one PR in skillforge. The workflow only runs once it is on `main`, so the live criteria are ticked
   with the first issue and PR after the merge.
3. **P0-2** - one PR each in skillsite and skillbot, independent of each other.
4. **P0-3** - settings, once P0-1 is proven. **P1-1** any time after P0-2.

## Rules for implementing agents

- Follow [`CLAUDE.md`](../../CLAUDE.md): English only, symbol references instead of line numbers, conventional
  commits, `just check` green before every commit, one PR per slice.
- Pin every action by full SHA with a version comment, as the existing workflows do.
- Never add a checkout step to `triage.yml`, and never use `${{ github.event.* }}` inside a `run:` block.
- App permissions, the org installation and the project's workflow settings are changed by Leon, not by an
  agent; describe the exact setting in the PR instead.
- Tick the acceptance checkboxes in this file in the PR that fulfils them and flip the status line when P0 is done.
