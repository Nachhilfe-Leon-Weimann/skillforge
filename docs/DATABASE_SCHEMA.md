# Database Schema

This document summarizes the SQLAlchemy database schema in a compact form for use as AI-agent context in other projects.

## Schemas

- `core`
- `geo`
- `ext`

## Shared Columns

Models using `TimestampMixin` include:

```sql
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

## Enums

```sql
core.party_type = ('person', 'company')
core.contact_info_type = ('email', 'phone')
core.party_relation_type = ('parent_of', 'tutor_of', 'pays_for')
core.preferred_meeting_tool = ('discord', 'in_person', 'microsoft_teams', 'phone')
```

## Core Schema

```sql
-- core.party
id UUID PRIMARY KEY DEFAULT uuid_generate_v4()
type party_type NOT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- core.person
party_id UUID PRIMARY KEY REFERENCES core.party(id) ON DELETE CASCADE
firstname TEXT NOT NULL
lastname TEXT NOT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- core.company
party_id UUID PRIMARY KEY REFERENCES core.party(id) ON DELETE CASCADE
name TEXT NOT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- core.student
person_id UUID PRIMARY KEY REFERENCES core.person(party_id) ON DELETE CASCADE
preferred_meeting_tool preferred_meeting_tool NOT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- core.tutor
person_id UUID PRIMARY KEY REFERENCES core.person(party_id) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- core.subject
id INTEGER PRIMARY KEY AUTOINCREMENT
title TEXT NOT NULL

-- core.student_subject
student_id UUID REFERENCES core.student(person_id) ON DELETE CASCADE
subject_id INTEGER REFERENCES core.subject(id) ON DELETE CASCADE
PRIMARY KEY (student_id, subject_id)

-- core.tutor_subject
tutor_id UUID REFERENCES core.tutor(person_id) ON DELETE CASCADE
subject_id INTEGER REFERENCES core.subject(id) ON DELETE CASCADE
PRIMARY KEY (tutor_id, subject_id)

-- core.contact_info
id UUID PRIMARY KEY DEFAULT uuid_generate_v4()
party_id UUID NOT NULL REFERENCES core.party(id) ON DELETE CASCADE
type contact_info_type NOT NULL
value TEXT NOT NULL
label TEXT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (party_id, type, value) -- uq_contact_info
INDEX (party_id)

-- core.party_relation
from_party_id UUID REFERENCES core.party(id) ON DELETE CASCADE
to_party_id UUID REFERENCES core.party(id) ON DELETE CASCADE
type party_relation_type NOT NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (from_party_id, to_party_id, type)
```

## Geo Schema

```sql
-- geo.plz_ort
plz VARCHAR(5) NOT NULL
ort TEXT NOT NULL
landkreis TEXT NOT NULL
bundesland TEXT NOT NULL
PRIMARY KEY (plz, ort)
```

## External Integrations Schema

```sql
-- ext.discord_account
discord_id TEXT PRIMARY KEY
party_id UUID NOT NULL UNIQUE REFERENCES core.party(id) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- ext.sevdesk_contact
sevdesk_id TEXT PRIMARY KEY
party_id UUID NOT NULL UNIQUE REFERENCES core.party(id) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- ext.clockodo_customer
clockodo_customer_id TEXT PRIMARY KEY
party_id UUID NOT NULL UNIQUE REFERENCES core.party(id) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- ext.clockodo_project
clockodo_project_id TEXT PRIMARY KEY
party_id UUID NOT NULL UNIQUE REFERENCES core.party(id) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- ext.microsoft_account
user_id TEXT PRIMARY KEY
party_id UUID NOT NULL UNIQUE REFERENCES core.party(id) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

-- ext.microsoft_contact
contact_id TEXT PRIMARY KEY
party_id UUID NOT NULL UNIQUE REFERENCES core.party(id) ON DELETE CASCADE
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

## Relationship Model

- `core.party` is the central entity.
- A `party` is either a `person` or a `company`, represented by `party.type`.
- `core.person.party_id` is both primary key and foreign key to `core.party.id`.
- `core.company.party_id` is both primary key and foreign key to `core.party.id`.
- `core.student` and `core.tutor` are roles of a `person`.
- `core.student_subject` and `core.tutor_subject` are many-to-many join tables between students/tutors and subjects.
- `core.contact_info` belongs to any `party`, so it can attach to both persons and companies.
- `core.party_relation` models directed relationships between parties, such as `parent_of`, `tutor_of`, and `pays_for`.
- Each `ext.*` table maps an external system identifier one-to-one to a `party`.
- Most foreign keys use `ON DELETE CASCADE`.

