# SkillBot DB Schema v2 Migration Proposal

This v2 proposal assumes:

- SkillBot has no existing persisted data.
- Inside SkillBot, `discord_id` is the stable user/principal id.
- Company/business data lives in the central company DB and is reached through
  Skillforge.
- `ext` is only for linking external identifiers to internal entities.
- Discord guilds, channels, command environments, and bot-specific permissions
  belong to the `bot` schema.
- The bot is operationally single-tenant, but Discord can install one bot on
  multiple guilds, so bot tables still include `guild_id` where the state is
  guild-scoped.

## Current Schema Fit

The existing `DATABASE_SCHEMA.md` is useful as the central company schema, but
not as the complete SkillBot schema.

Keep as-is or near as-is:

- `core.party`, `core.person`, `core.company`
- `core.student`, `core.tutor`, `core.subject`
- `core.contact_info`
- `core.party_relation`
- `ext.discord_account` as a Discord-account-to-party link
- other `ext.*` integration links

Missing for SkillBot:

- Bot-local role state for Discord users: `admin`, `teacher`, `student`.
- Bot-local permission groups and permission grants.
- Guild-scoped command environment whitelists.
- Teacher workspaces: category channel and teacher command channel.
- Optional student workspaces: student channel and assigned teacher.
- Discord role bindings per guild.
- Minimal Discord guild/channel metadata needed by the bot.

Do not add:

- A separate `core.app_user` / `user_id` layer only for the bot.
- Discord guilds/channels/roles in `ext`; they are bot operational state, not
  company identity links.
- Full mirrors of Discord users, members, roles, channels, or guild data.

## Schemas

- `core`: existing central business schema
- `geo`: existing geo schema
- `ext`: external id links to internal business entities
- `bot`: SkillBot-owned operational state

No separate `auth` schema is needed for the current bot. Permissions are
bot-specific and use Discord ids as principals.

## Shared Columns

```sql
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

Use `active` only for long-lived identity/configuration rows that may be
disabled without losing their history. Workflow rows should use specific state
columns, and Discord resources that disappear should use `deleted_at` or be
deleted.

## Enums

```sql
-- existing / central
core.party_type = ('person', 'company')
core.contact_info_type = ('email', 'phone')
core.party_relation_type = ('parent_of', 'tutor_of', 'pays_for')
core.preferred_meeting_tool = ('discord', 'in_person', 'microsoft_teams', 'phone')

-- bot-owned
bot.member_role = ('admin', 'teacher', 'student')
bot.permission_subject_type = ('role', 'group', 'user')
bot.permission_grant_effect = ('allow', 'deny')
bot.command_env_kind = ('admin_cmd', 'teacher_cmd')
bot.discord_channel_type = ('category', 'text', 'voice', 'thread', 'forum')
bot.student_channel_state = ('teacher_category', 'archive_category')
```

## Core Schema Changes

No core schema change is required for the bot itself.

If the company DB does not already have a customer-to-party selection model,
Skillforge needs one for `/bot/students/activate(customer_id=...)`. That belongs
to the company domain, not the bot domain:

```sql
-- optional central-company addition, only if not already covered elsewhere
-- core.customer_party
customer_id BIGINT NOT NULL
party_id UUID NOT NULL REFERENCES core.party(id) ON DELETE CASCADE
role TEXT NOT NULL              -- e.g. student, parent, payer, contact
is_primary BOOLEAN NOT NULL DEFAULT false
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (customer_id, party_id, role)
INDEX (customer_id, role, is_primary)
INDEX (party_id)
```

SkillBot should not own this table. The bot only passes `customer_id`;
Skillforge resolves the matching `party_id`.

## External Integrations Schema

Keep `ext.discord_account` as the company identity link. It should not store
guilds, channels, nicknames, role config, command environments, or permission
state.

Recommended shape:

```sql
-- ext.discord_account
discord_id BIGINT PRIMARY KEY
party_id UUID NOT NULL REFERENCES core.party(id) ON DELETE CASCADE
label TEXT NULL
is_primary BOOLEAN NOT NULL DEFAULT false
active BOOLEAN NOT NULL DEFAULT true
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
INDEX (party_id, active)
UNIQUE (party_id) WHERE is_primary = true AND active = true
```

This allows several Discord accounts per party while preserving a single active
primary account when company-side workflows need a deterministic default. The
bot still uses `discord_id` as its own principal key, so no bot table needs a
party-level uniqueness assumption.

## Bot Schema

### Bot Users

`bot.discord_user` is the bot-local principal table. It intentionally uses
`discord_id` as primary key.

```sql
-- bot.discord_user
discord_id BIGINT PRIMARY KEY
role bot.member_role NOT NULL
full_name TEXT NOT NULL
active BOOLEAN NOT NULL DEFAULT true
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
CHECK (full_name <> '')
INDEX (role, active)
```

Notes:

- `discord_id` can be joined to `ext.discord_account.discord_id` when company
  party data is needed.
- There is no FK to `ext.discord_account`, so the bot can temporarily know a
  Discord user before Skillforge has linked the party. Activation should still
  create/update the ext link when party resolution succeeds.

### Guilds And Channels

Store only the Discord resources the bot needs to recover or authorize its own
state. Do not mirror all guild members or all channels.

```sql
-- bot.discord_guild
guild_id BIGINT PRIMARY KEY
name TEXT NULL
is_primary BOOLEAN NOT NULL DEFAULT false
active BOOLEAN NOT NULL DEFAULT true
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (is_primary) WHERE is_primary = true AND active = true

-- bot.discord_channel
channel_id BIGINT PRIMARY KEY
guild_id BIGINT NOT NULL REFERENCES bot.discord_guild(guild_id) ON DELETE CASCADE
parent_channel_id BIGINT NULL
type bot.discord_channel_type NOT NULL
name TEXT NULL
managed_by_bot BOOLEAN NOT NULL DEFAULT true
deleted_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (guild_id, channel_id)
FOREIGN KEY (guild_id, parent_channel_id)
  REFERENCES bot.discord_channel(guild_id, channel_id)
  ON DELETE SET NULL
INDEX (guild_id, deleted_at)
INDEX (parent_channel_id)
```

Single-tenant interpretation:

- `is_primary` lets operations assume one normal guild.
- `guild_id` remains part of guild-scoped rows so accidental installation on a
  second guild does not corrupt command environments or workspaces.

### Discord Role Bindings

Role bindings keep guild-specific Discord role ids/names out of code. They can
be seeded with the current defaults (`Admin`, `Lehrer`, `Schüler`) and then
changed per guild if needed.

```sql
-- bot.discord_role_binding
guild_id BIGINT NOT NULL REFERENCES bot.discord_guild(guild_id) ON DELETE CASCADE
member_role bot.member_role NOT NULL
role_id BIGINT NULL
role_name TEXT NOT NULL
active BOOLEAN NOT NULL DEFAULT true
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (guild_id, member_role)
```

### Teacher Workspace

Teacher-specific Discord category/channel state belongs to the bot.

```sql
-- bot.teacher_workspace
guild_id BIGINT NOT NULL REFERENCES bot.discord_guild(guild_id) ON DELETE CASCADE
teacher_discord_id BIGINT NOT NULL REFERENCES bot.discord_user(discord_id) ON DELETE CASCADE
category_channel_id BIGINT NOT NULL
command_channel_id BIGINT NOT NULL
student_channel_capacity SMALLINT NOT NULL DEFAULT 49
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (guild_id, teacher_discord_id)
FOREIGN KEY (guild_id, category_channel_id)
  REFERENCES bot.discord_channel(guild_id, channel_id)
  ON DELETE RESTRICT
FOREIGN KEY (guild_id, command_channel_id)
  REFERENCES bot.discord_channel(guild_id, channel_id)
  ON DELETE RESTRICT
UNIQUE (category_channel_id)
UNIQUE (command_channel_id)
CHECK (student_channel_capacity BETWEEN 0 AND 49)
```

This backs the current `Teacher` DTO:

- `discord_id` = `teacher_discord_id`
- `full_name` = `bot.discord_user.full_name`
- `role` = `bot.discord_user.role`
- `teaching_category_id` = `category_channel_id`
- `command_channel_id` = `command_channel_id`

`student_channel_capacity` defaults to `49` because the teacher category also
contains the `cmd` channel. Lower it if the bot creates additional managed
channels inside the teacher category.

### Guild Archive Categories

Archive categories are bot-managed overflow containers for stashed student
channels. They are scoped to the guild, not to a teacher. Teachers still only
see the archived channels relevant to them because the bot preserves or rewrites
per-channel permission overwrites when moving channels into a shared archive
category.

```sql
-- bot.archive_category
guild_id BIGINT NOT NULL REFERENCES bot.discord_guild(guild_id) ON DELETE CASCADE
archive_no INTEGER NOT NULL
category_channel_id BIGINT NOT NULL
capacity SMALLINT NOT NULL DEFAULT 50
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (guild_id, archive_no)
FOREIGN KEY (guild_id, category_channel_id)
  REFERENCES bot.discord_channel(guild_id, channel_id)
  ON DELETE RESTRICT
UNIQUE (category_channel_id)
UNIQUE (guild_id, category_channel_id)
INDEX (guild_id)
CHECK (archive_no > 0)
CHECK (capacity BETWEEN 1 AND 50)
```

The current fill level should usually be derived from stashed
`bot.student_workspace` rows instead of stored redundantly:

```sql
COUNT(*)
FROM bot.student_workspace sw
JOIN bot.discord_channel ch
  ON ch.guild_id = sw.guild_id
 AND ch.channel_id = sw.channel_id
WHERE sw.archive_category_channel_id = :category_channel_id
  AND sw.channel_state = 'archive_category'
  AND ch.deleted_at IS NULL
```

### Student Workspace

Student identity is split:

- Company/student identity is `core.party` via `ext.discord_account`.
- Bot-local identity is `bot.discord_user`.
- Guild-specific bot workspace/assignment is here.

```sql
-- bot.student_workspace
guild_id BIGINT NOT NULL REFERENCES bot.discord_guild(guild_id) ON DELETE CASCADE
student_discord_id BIGINT NOT NULL REFERENCES bot.discord_user(discord_id) ON DELETE CASCADE
teacher_discord_id BIGINT NOT NULL REFERENCES bot.discord_user(discord_id) ON DELETE RESTRICT
channel_id BIGINT NULL
channel_state bot.student_channel_state NOT NULL DEFAULT 'teacher_category'
current_parent_channel_id BIGINT NULL
archive_category_channel_id BIGINT NULL
stashed_at TIMESTAMPTZ NULL
popped_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (guild_id, student_discord_id)
UNIQUE (channel_id)
FOREIGN KEY (guild_id, channel_id)
  REFERENCES bot.discord_channel(guild_id, channel_id)
  ON DELETE SET NULL
FOREIGN KEY (guild_id, current_parent_channel_id)
  REFERENCES bot.discord_channel(guild_id, channel_id)
  ON DELETE RESTRICT
FOREIGN KEY (guild_id, archive_category_channel_id)
  REFERENCES bot.archive_category(guild_id, category_channel_id)
  ON DELETE RESTRICT
INDEX (guild_id, teacher_discord_id)
INDEX (guild_id, teacher_discord_id, channel_state)
INDEX (guild_id, channel_state)
INDEX (archive_category_channel_id, channel_state)
CHECK (
  (channel_state = 'teacher_category' AND archive_category_channel_id IS NULL)
  OR (channel_state = 'archive_category' AND archive_category_channel_id IS NOT NULL)
)
CHECK (
  channel_state <> 'archive_category'
  OR channel_id IS NOT NULL
)
CHECK (
  channel_state <> 'archive_category'
  OR current_parent_channel_id = archive_category_channel_id
)
CHECK (
  channel_state <> 'teacher_category'
  OR channel_id IS NULL
  OR current_parent_channel_id IS NOT NULL
)
```

This backs the current `Student` DTO:

- `discord_id` = `student_discord_id`
- `full_name` = `bot.discord_user.full_name`
- `party_id` = resolved by Skillforge through `ext.discord_account`
- `teacher_discord_id` = `teacher_discord_id`
- `channel_id` = `channel_id`

Stash/pop behavior:

- `stash`: move the Discord channel from the teacher category to an archive
  category, set `channel_state = 'archive_category'`, set
  `current_parent_channel_id` and `archive_category_channel_id` to that archive
  category, and set `stashed_at = now()`.
- `pop`: move the Discord channel back to `bot.teacher_workspace.category_channel_id`,
  set `channel_state = 'teacher_category'`, set `current_parent_channel_id` to
  the teacher category, clear `archive_category_channel_id`, and set
  `popped_at = now()`.
- Before `pop`, count `teacher_category` student rows with non-deleted channels
  for the teacher and compare with `student_channel_capacity`.
- Before `stash`, pick the lowest archive category with remaining guild-wide
  capacity; if none exists, create `archive_no = max(archive_no) + 1` for that
  guild.

### Command Environments

Command environments are guild-scoped bot policy.

```sql
-- bot.command_env_channel
guild_id BIGINT NOT NULL REFERENCES bot.discord_guild(guild_id) ON DELETE CASCADE
channel_id BIGINT NOT NULL
kind bot.command_env_kind NOT NULL
owner_discord_id BIGINT NULL REFERENCES bot.discord_user(discord_id) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (guild_id, channel_id, kind)
FOREIGN KEY (guild_id, channel_id)
  REFERENCES bot.discord_channel(guild_id, channel_id)
  ON DELETE CASCADE
INDEX (guild_id, kind)
INDEX (guild_id, owner_discord_id, kind)
UNIQUE (guild_id, owner_discord_id, kind)
  WHERE owner_discord_id IS NOT NULL
```

This replaces `owner_user_id` with `owner_discord_id`.

### Permissions

Permissions are bot-local and use these subject keys:

- `role`: one of `admin`, `teacher`, `student`
- `group`: `bot.permission_group.key`
- `user`: Discord snowflake as text

```sql
-- bot.permission_group
key TEXT PRIMARY KEY
name TEXT NOT NULL
active BOOLEAN NOT NULL DEFAULT true
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- bot.discord_user_permission_group
discord_id BIGINT NOT NULL REFERENCES bot.discord_user(discord_id) ON DELETE CASCADE
group_key TEXT NOT NULL REFERENCES bot.permission_group(key) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (discord_id, group_key)

-- bot.permission_grant
id UUID PRIMARY KEY DEFAULT uuid_generate_v4()
subject_type bot.permission_subject_type NOT NULL
subject_key TEXT NOT NULL
action_key TEXT NOT NULL
effect bot.permission_grant_effect NOT NULL
priority INTEGER NOT NULL DEFAULT 0
active BOOLEAN NOT NULL DEFAULT true
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (subject_type, subject_key, action_key)
INDEX (subject_type, subject_key, active)
INDEX (action_key)
```

### Audit Log

The current logger writes to application logs only. Persisting command audit
events is optional, but if wanted it belongs to `bot`.

```sql
-- bot.app_command_audit_log
id UUID PRIMARY KEY DEFAULT uuid_generate_v4()
guild_id BIGINT NULL
channel_id BIGINT NULL
discord_id BIGINT NULL
command_name TEXT NOT NULL
permission_action TEXT NULL
permission_allowed BOOLEAN NULL
permission_source TEXT NULL
permission_reason TEXT NULL
error_type TEXT NULL
error TEXT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
INDEX (created_at)
INDEX (guild_id, command_name, created_at)
INDEX (discord_id, created_at)
```

## Skillforge API Contract Changes

Because the bot models are still in development, align the API with Discord ids
now and remove `user_id` from the bot-facing contract.

Recommended DTOs:

```python
class BotUser:
    discord_id: int
    full_name: str
    role: MemberRole


class Teacher:
    discord_id: int
    full_name: str
    teaching_category_id: int | None = None
    command_channel_id: int | None = None


class Student:
    discord_id: int
    full_name: str
    party_id: UUID
    teacher_discord_id: int
    channel_id: int | None = None
    channel_state: StudentChannelState = StudentChannelState.teacher_category


class CommandEnvChannel:
    guild_id: int
    channel_id: int
    kind: CommandEnvKind
    owner_discord_id: int | None = None


class ActivateTeacherRequest:
    guild_id: int
    discord_id: int
    full_name: str
    teaching_category_id: int
    command_channel_id: int


class ActivateStudentRequest:
    guild_id: int
    teacher_discord_id: int
    student_discord_id: int
    full_name: str
    customer_id: int
```

Recommended endpoint changes:

- Replace `/bot/users/by-discord/{discord_id}` with direct use of
  `discord_id`, or keep it only as an existence lookup.
- Replace `owner_user_id` request/response fields with `owner_discord_id`.
- Replace `teacher_user_id` request/response fields with `teacher_discord_id`.
- Add `guild_id` to teacher and student activation requests.
- Keep `party_id` in `Student` responses, resolved through Skillforge and
  `ext.discord_account`.
- Add bot endpoints for stashing/popping student channels:
  - `POST /bot/students/{guild_id}/{student_discord_id}/stash`
  - `POST /bot/students/{guild_id}/{student_discord_id}/pop`
  - optionally `GET /bot/guilds/{guild_id}/archives`

## SkillBot Query Mapping

- `get_teacher_by_discord_id(discord_id)`:
  active `bot.discord_user` plus `bot.teacher_workspace`.
- `get_student_by_discord_id(discord_id)`:
  active `bot.discord_user` plus `bot.student_workspace`, and `party_id` from
  active `ext.discord_account`.
- `list_teacher_discord_ids()`:
  active `bot.discord_user` rows with `role = 'teacher'`.
- `list_student_discord_ids()`:
  active `bot.discord_user` rows with `role = 'student'`.
- `activate_teacher(request)`:
  upsert `bot.discord_guild`, `bot.discord_user`, category/cmd
  `bot.discord_channel` rows, and `bot.teacher_workspace`.
- `activate_student(request)`:
  Skillforge resolves `customer_id` to `party_id`; upsert
  `ext.discord_account`, `bot.discord_user`, and `bot.student_workspace`.
- `stash_student(guild_id, student_discord_id)`:
  lock the student workspace, find/create a guild-wide `bot.archive_category`
  with remaining capacity, move the Discord channel, and update the workspace
  to `channel_state = 'archive_category'`.
  Preserve or set channel-level permission overwrites so the assigned teacher
  can still see the channel inside the shared archive category.
- `pop_student(guild_id, student_discord_id)`:
  lock the student workspace, ensure the teacher category has remaining
  `student_channel_capacity`, move the Discord channel back, and update the
  workspace to `channel_state = 'teacher_category'`.
- `get_permission_principal(discord_user_id)`:
  read `bot.discord_user` and groups from `bot.discord_user_permission_group`.
- `list_permission_grants(subjects)`:
  read active `bot.permission_grant`.
- `get_command_env_channel(...)`:
  read `bot.command_env_channel`.
- `get_owner_command_env_channel_id(...)`:
  read `bot.command_env_channel` by `owner_discord_id`.
- `upsert_command_env_channel(...)`:
  upsert `bot.discord_guild`, `bot.discord_channel`, then
  `bot.command_env_channel`.

## Implementation Invariants

The schema intentionally keeps Discord operational state small. A few rules are
therefore enforced by Skillforge/the bot service rather than by cross-table SQL
checks:

- `bot.teacher_workspace.category_channel_id` and
  `bot.archive_category.category_channel_id` must point to Discord category
  channels.
- `bot.teacher_workspace.command_channel_id`,
  `bot.student_workspace.channel_id`, and `bot.command_env_channel.channel_id`
  must point to text channels where the command/workspace expects text.
- Queries that operate on Discord channels should ignore rows where
  `bot.discord_channel.deleted_at IS NOT NULL`.
- A managed channel should be represented in exactly one owning table for its
  primary purpose, except command-env rows which may intentionally point at an
  existing command channel.
- A `teacher_discord_id` in teacher/student workspaces should refer to an active
  `bot.discord_user` with `role = 'teacher'`.
- A `student_discord_id` in student workspaces should refer to an active
  `bot.discord_user` with `role = 'student'`.
- `stash` and `pop` must lock the affected `bot.student_workspace` row. `stash`
  should also lock candidate `bot.archive_category` rows while checking
  capacity, so concurrent stashes do not overfill the same Discord category.
- Archive fill level is derived, not stored. This avoids a counter repair
  migration later.
- To disable a workflow row such as a command environment or workspace, delete
  the row or move it to a future explicit status model; do not add generic
  `active` flags.

## Coverage Check

For the currently described SkillBot scope, this schema covers:

- teacher activation with guild-scoped category and `cmd` channel
- student activation linked to a company `party_id` through Skillforge and
  `ext.discord_account`
- multiple Discord accounts per party
- bot-local roles, groups, and permission grants
- command environment whitelisting including owner-bound teacher channels
- guild-wide archive categories for stashed student channels
- stash/pop without losing teacher ownership or channel visibility
- accidental multi-guild installation without treating guilds as hard tenants
- optional persisted command audit events

No additional bot-schema migration should be needed for the features listed
above. Future migrations would only be expected for genuinely new product
requirements, such as lesson scheduling, billing, message transcripts, or a
central website login/auth model.

## Migration Plan

Because the bot has no data, this can be a structural migration.

1. Create schema `bot`.
2. Create bot enums:
   `member_role`, `permission_subject_type`, `permission_grant_effect`,
   `command_env_kind`, `discord_channel_type`, `student_channel_state`.
3. Ensure `ext.discord_account(discord_id, party_id)` exists in the company DB.
   Do not add guild/channel/role tables to `ext`.
4. Create `bot.discord_user`.
5. Create `bot.discord_guild` and `bot.discord_channel`.
6. Create `bot.discord_role_binding`.
7. Create `bot.teacher_workspace`.
8. Create `bot.archive_category`.
9. Create `bot.student_workspace`.
10. Create `bot.command_env_channel`.
11. Create permission tables:
   `bot.permission_group`, `bot.discord_user_permission_group`,
   `bot.permission_grant`.
12. Seed baseline grants, for example:
    - `role:admin` allow `*`
    - `role:teacher` allow `students.enable`
    - `role:teacher` deny `teachers.*`
13. Update SkillBot DTOs and Skillforge endpoints to use Discord ids instead
    of bot `user_id`.
14. Add `guild_id` to activation requests before persisting teacher/student
    workspace rows.
15. Add stash/pop endpoints and keep channel moves plus DB updates in one
    compensating workflow: if Discord move fails, do not update DB; if DB update
    fails after Discord move, move the channel back.

## Main Open Decision

The only central-company model still unclear is `customer_id -> party_id`
resolution. If Skillforge already has a customer membership model, use that and
keep it out of the bot schema. If not, add a central `core.customer_party`-style
mapping in the company DB; do not put customer membership into `bot`.
