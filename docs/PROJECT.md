# Forge - project sketch

What Forge is for, where its borders are, and the principles every change is measured against.
Kept short on purpose, so it can be reread before any larger piece of work. When the intent
changes, it changes here first; ADRs and specs follow.

## In one paragraph

Forge is the central service of the skill platform. It keeps the data the whole tutoring business
relies on - who the people are and how they relate: students, guardians, payers, tutors, companies -
and it decides who may do what with that data. Showing that data to people is somebody else's job.
The Discord bot (SkillBot) and the web portal (skillsite) are frontends with their own backends and
their own storage; they come to Forge when they need central data or a decision. **Forge is the hub
for central data, not the backend of any single frontend.**

## The platform at a glance

```mermaid
flowchart LR
    people([Students, guardians, tutors, admins])
    operator([Operator])
    bot["SkillBot<br/>Discord commands"]
    portal["Portal<br/>skillsite, Next.js server"]
    forge["Forge<br/>central data, identity,<br/>permissions, domain rules"]
    botdb[(Bot database)]
    forgedb[(Forge database)]

    people -- Discord --> bot
    people -- browser --> portal
    operator -- Swagger UI --> forge
    bot --- botdb
    bot -- "API: for a person or for itself" --> forge
    portal -- "API: for a person" --> forge
    forge --- forgedb
```

This is the target picture. Parts of today's code still look different - see
[Where we are](#where-we-are).

## What Forge owns - and what it does not

| Forge owns                                                                                      | The frontends own                                                                                                     |
| ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| People and organisations, their roles and relations (the CRM)                                   | How things look and feel: pages, commands, messages                                                                   |
| Identities: user accounts, how people log in, links to outside accounts (Discord, sevDesk, ...) | Their sessions and screen state                                                                                       |
| Permissions: which client and which person may do what                                          | Their own picture of the data - for the bot: servers, channels, categories, workspaces, command setups, Discord roles |
| Domain rules and actions that mean something for the business                                   | Rules that only concern their own medium                                                                              |

The test for a rule: **would the other frontend need the same rule?** Then it belongs in Forge.
"Only a student's own tutor may act on the student" is a Forge rule. "How many archive categories
a Discord server holds" is a bot rule.

## Principles

1. **Forge is the hub, not a backend.** Frontends ask Forge for central data and decisions. They do
   not keep their own copy of central data, and Forge does not keep theirs.
2. **Every service owns its database.** Central data is reached only through Forge's API, never by
   reading another service's tables. The bot gets its own database on the same Postgres server.
3. **The CRM is the system of record.** It holds the intended state of the business and knows
   nothing about Discord or logins. Everybody reads it; nobody writes around it
   ([ADR 0007](decisions/0007-crm-system-of-record.md)).
4. **One permission system.** Every endpoint declares once what it requires. There is no second,
   frontend-specific rights system, and what was not deliberately opened stays closed.
5. **Same person, same rights - everywhere.** Whether a tutor acts in the portal or through a
   Discord command, Forge decides by the same rules.
6. **The client is the ceiling.** A frontend can never do more for a person than it is allowed to do
   for people at all - and a person can never do more through a frontend than the person may do.
7. **The account is the door.** Only people with a user account use authenticated features. Admins
   create accounts, always for a person who exists in the CRM.
8. **The API is a contract.** `openapi.json` is generated and versioned; the frontends build on it
   ([ADR 0001](decisions/0001-openapi-as-contract.md)).

## How permissions work, in plain words

Three questions decide every request.

1. **Which frontend is asking?** Every frontend is a registered _client_ with its own secret. Its
   _grants_ say what it may do, and each grant has a mode:
   - _for itself_ - work no person asked for, such as routine jobs;
   - _on behalf of people_ - the most it may ever do for any person.
2. **For whom?** A frontend that acts for a person presents a token for that person's account. The
   portal gets one when the person logs in with e-mail and password. The bot gets one by telling
   Forge which Discord user sent the command - something only clients explicitly allowed to do so
   may do.
3. **What may that person do?** That follows from the person's _roles_. Student, guardian and tutor
   come from the CRM; admin is assigned. Every person may manage their own account and read their
   own data.

A token carries the overlap of both sides:

```
what the frontend may do for people  ∩  what the person may do
```

A frontend may ask for less - the bot, say, only for what one command needs. It never gets more.

"Their own data" is precise: a person's _reach_ is their own party plus the parties they are parent
of or pay for. Tutors reaching their students follows later, together with rules for what a tutor
may see - who pays for a student, for example, is none of the tutor's business.

## Where we are

As of 2026-09.

| Topic           | Today                                                                                        | Target                                                                                             |
| --------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Bot state       | Lives in Forge's `bot` schema; Forge plans Discord changes (two-phase operations, job queue) | The bot keeps it in its own database; Forge provides central data and decisions only               |
| Bot permissions | The bot calls Forge as itself; its own grant system decides what a Discord user may do       | The bot acts on behalf of the Discord user; Forge decides by the same rules as for the portal      |
| People          | No user accounts; only applications log in (client credentials)                              | One account per person, created by admins; e-mail and password for the portal, Discord for the bot |
| Client grants   | One list per client, used for the client itself                                              | Every grant has a mode: for itself, or on behalf of people                                         |
| Own data        | A scope such as `crm:read` always means every record                                         | `:own` scopes limit a person to their reach                                                        |
| Portal          | Not started                                                                                  | A Next.js server backend that talks to Forge                                                       |

## Roadmap

Coarse on purpose; the details live in the GitHub project.

1. **Auth core** - now. User accounts, client grants with modes, portal login (e-mail and
   password, refresh, logout) and "own data" on the party read routes. After this the portal can
   start. Its spec and ADR are being written.
2. **Bot arc.** The bot's state moves into its own database, bot commands run on the person's
   token (Discord as a way to log in), tutors reach their students, and the bot's own grant system
   retires.
3. **Portal arc** in skillsite: its backend and the first views. Can run alongside the bot arc.
4. **Later:** sending e-mail (invitations, password resets), self-service for people, lessons.

## Words we use

- **Party**: a person or an organisation in the CRM. Everyone Forge knows is a party.
- **Client**: a registered application - SkillBot, the portal, an operator tool - with its own secret.
- **Account**: a person's access to authenticated features. Belongs to exactly one person party.
- **Scope**: the name of a permission an endpoint requires, such as `crm:read`.
- **Grant**: a scope given to a client, for itself or on behalf of people.
- **Role**: what a person is - student, guardian, tutor, admin. Roles decide which scopes a person has.
- **Reach**: the parties a person may see through an `:own` scope such as `crm:read:own` - themselves,
  and those they are parent of or pay for.
- **Token**: short-lived proof a client sends with every request, saying who is asking and what they may do.

## How this sketch fits in

- **This sketch** - the intent: what Forge is for, its borders, its principles. Changes rarely.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) - what exists now and how it is built.
- [`decisions/`](decisions/) - one record per decision, with the context it was made in.
- [`specs/`](specs/) - work orders for one arc each, detailed enough for an implementing agent.

When an ADR or a spec contradicts this sketch, the sketch wins - or it is changed on purpose, here,
first.
