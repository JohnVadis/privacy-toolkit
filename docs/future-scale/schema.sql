-- =============================================================================
-- Privacy Case Management + Exposure Discovery — CORE SCHEMA (state-agnostic)
-- Target: PostgreSQL 15+  (Supabase-compatible)
-- Draft v0.1
--
-- Design notes:
--   * This file is the STATE-AGNOSTIC core. Jurisdiction specifics (exemptions,
--     sources, routing rules) live in per-state seed files, e.g. seed_florida.sql.
--   * The router is a deterministic rules TABLE (routing_rules) — no LLM. An
--     exposure resolves to a workflow via v_exposure_routing. Token cost: zero.
--   * PII is concentrated here. Columns marked [SENSITIVE] should be encrypted
--     at rest (pgcrypto / Supabase Vault / app-layer envelope encryption) and
--     access-controlled by RLS. SSN is intentionally NOT stored — the FL redaction
--     workflow does not require it.
--   * Every mutation should write to audit_log. Every status change writes a
--     status_events row. Both are append-only (immutable) for the GRC/audit trail.
-- =============================================================================

create extension if not exists "pgcrypto";   -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- Enumerated types
-- ---------------------------------------------------------------------------

create type person_role as enum (
  'primary','spouse','child','dependent','relative','associate','business'
);

create type identifier_kind as enum (
  'alias','phone','email','dob','employer','business_name','username','other'
);

create type source_category as enum (
  'gov_clerk','gov_appraiser','gov_corporate','gov_dmv','gov_other',
  'property_site','data_broker','search_engine','social_professional','other'
);

create type exposure_classification as enum (
  'unclassified','keep','investigate','remove','suppress'
);

-- One status ladder shared by exposures and actions.
create type workflow_status as enum (
  'discovered','needs_review','actionable','submitted',
  'verification_required','pending','removed','partially_removed',
  'rejected','not_removable','no_action'
);

create type action_type as enum (
  'redaction_request','confidentiality_request','opt_out',
  'record_change','deindex_request','keep'
);

create type submission_method as enum (
  'online_portal','agent_portal','email','mail','fax','api','manual'
);

-- ---------------------------------------------------------------------------
-- Reference: counties (US, county-level routing for gov sources)
-- ---------------------------------------------------------------------------

create table counties (
  id          uuid primary key default gen_random_uuid(),
  state_code  char(2) not null,
  name        text    not null,
  created_at  timestamptz not null default now(),
  unique (state_code, name)
);

-- ---------------------------------------------------------------------------
-- Cases (one per client engagement) and linked persons
-- ---------------------------------------------------------------------------

create table cases (
  id                    uuid primary key default gen_random_uuid(),
  case_number           text unique,                    -- human-facing ID
  display_name          text not null,                  -- e.g. "Doe, John"
  status                text not null default 'open',   -- open | on_hold | closed
  acting_as_agent       boolean not null default true,  -- authorized agent for subject?
  authorization_doc_uri text,                            -- signed agreement (encrypted store)
  opened_at             timestamptz not null default now(),
  closed_at             timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create table persons (
  id          uuid primary key default gen_random_uuid(),
  case_id     uuid not null references cases(id) on delete cascade,
  role        person_role not null default 'primary',
  first_name  text,
  middle_name text,
  last_name   text,
  suffix      text,
  dob         date,          -- [SENSITIVE] encrypt at rest
  notes       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index on persons (case_id);

-- Structured addresses. County is required for gov routing; residence flag drives
-- which exposures are high-priority (home-address leakage).
create table addresses (
  id           uuid primary key default gen_random_uuid(),
  person_id    uuid not null references persons(id) on delete cascade,
  line1        text,          -- [SENSITIVE]
  line2        text,
  city         text,
  county_id    uuid references counties(id),
  state_code   char(2),
  postal_code  text,
  is_current   boolean not null default true,
  is_residence boolean not null default true,
  created_at   timestamptz not null default now()
);
create index on addresses (person_id);
create index on addresses (county_id);

-- Phones, emails, aliases, employers, business names, usernames, DOB-as-string, etc.
create table identifiers (
  id          uuid primary key default gen_random_uuid(),
  person_id   uuid not null references persons(id) on delete cascade,
  kind        identifier_kind not null,
  value       text not null,  -- [SENSITIVE] for phone/email
  label       text,
  is_primary  boolean not null default false,
  created_at  timestamptz not null default now()
);
create index on identifiers (person_id, kind);

-- ---------------------------------------------------------------------------
-- Exemptions: catalog (seeded per state) + per-case claims
-- ---------------------------------------------------------------------------

-- Catalog of statutory bases a case can claim. State-scoped, human-maintained.
create table exemptions (
  id                       uuid primary key default gen_random_uuid(),
  state_code               char(2) not null,
  code                     text not null,          -- short key, e.g. 'FL_119_071_4d'
  title                    text not null,
  statute_cite             text,                   -- e.g. 's. 119.071(4)(d), F.S.'
  description              text,
  requires_notarization    boolean not null default true,
  requires_supporting_doc  boolean not null default true,
  covers_family            boolean not null default false,
  active                   boolean not null default true,
  unique (state_code, code)
);

-- A case's claimed exemption(s). ELIGIBILITY IS HUMAN-GATED: human_verified must
-- be true before any request that asserts this exemption is submitted.
create table case_exemptions (
  id                    uuid primary key default gen_random_uuid(),
  case_id               uuid not null references cases(id) on delete cascade,
  exemption_id          uuid not null references exemptions(id),
  covers_person_id      uuid references persons(id),   -- who this exemption protects
  supporting_doc_uri    text,
  human_verified        boolean not null default false, -- <-- eligibility gate
  verified_by           text,
  verified_at           timestamptz,
  notes                 text,
  created_at            timestamptz not null default now()
);
create index on case_exemptions (case_id);

-- ---------------------------------------------------------------------------
-- Sources: catalog of where PII appears (the routing target)
-- ---------------------------------------------------------------------------

create table sources (
  id                 uuid primary key default gen_random_uuid(),
  key                text unique not null,     -- 'zillow','fl_sunbiz','fl_clerk_official_records'
  name               text not null,
  category           source_category not null,
  jurisdiction_state char(2),                   -- for gov sources
  county_scoped      boolean not null default false,  -- clerk/appraiser vary by county
  opt_out_url        text,
  notes              text,
  active             boolean not null default true,
  created_at         timestamptz not null default now()
);
create index on sources (category);

-- ---------------------------------------------------------------------------
-- Exposures: a discovered instance of a person's PII at a source
-- ---------------------------------------------------------------------------

create table exposures (
  id               uuid primary key default gen_random_uuid(),
  case_id          uuid not null references cases(id) on delete cascade,
  person_id        uuid not null references persons(id),
  source_id        uuid not null references sources(id),
  county_id        uuid references counties(id),    -- for county-scoped gov sources
  locator_url      text,                             -- broker/property/record URL
  classification   exposure_classification not null default 'unclassified',
  status           workflow_status not null default 'discovered',
  priority         smallint not null default 3,      -- 1 = highest .. 5 = lowest
  data_exposed     text[] not null default '{}',     -- {home_address,phone,dob,photo,...}
  evidence_uri     text,                             -- screenshot / captured page

  -- Public-record specifics (nullable; populated for gov_clerk / gov_appraiser)
  instrument_number text,
  book              text,
  page              text,
  record_date       date,
  doc_type          text,

  discovered_at    timestamptz not null default now(),
  discovered_by    text,                             -- 'crawler:name_search' | user id
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index on exposures (case_id, status);
create index on exposures (source_id);
create index on exposures (classification);

-- ---------------------------------------------------------------------------
-- Actions: a remediation attempt against one exposure
-- ---------------------------------------------------------------------------

create table actions (
  id                    uuid primary key default gen_random_uuid(),
  exposure_id           uuid not null references exposures(id) on delete cascade,
  action_type           action_type not null,
  form_template_key     text,                          -- which template to generate
  submission_method     submission_method,
  requires_notarization boolean not null default false,
  requires_human_review boolean not null default true, -- <-- review gate
  auto_submit_allowed   boolean not null default false,
  supporting_doc_uri    text,
  packet_uri            text,                          -- generated, ready-to-submit PDF
  submitted_at          timestamptz,
  confirmation_ref      text,                          -- agency/site reference number
  next_follow_up        date,
  status                workflow_status not null default 'needs_review',
  assigned_to           text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index on actions (status);
create index on actions (next_follow_up);

-- ---------------------------------------------------------------------------
-- Append-only trails
-- ---------------------------------------------------------------------------

-- Status ladder transitions for exposures/actions (worker-visible history).
create table status_events (
  id           bigserial primary key,
  entity_type  text not null,          -- 'exposure' | 'action'
  entity_id    uuid not null,
  from_status  workflow_status,
  to_status    workflow_status not null,
  note         text,
  actor        text,                    -- user id or system component
  created_at   timestamptz not null default now()
);
create index on status_events (entity_type, entity_id);

-- Immutable audit log for every mutation/export/submission (GRC / ISO 42001).
create table audit_log (
  id           bigserial primary key,
  actor        text,
  action       text not null,          -- insert|update|delete|export|submit|login
  entity_type  text,
  entity_id    uuid,
  detail       jsonb,
  created_at   timestamptz not null default now()
);
create index on audit_log (entity_type, entity_id);
create index on audit_log (created_at);

-- ---------------------------------------------------------------------------
-- Routing rules: the deterministic router
--   Match precedence (most specific wins):
--     1. exact match_source_key
--     2. match_source_category
--     3. catch-all (both null)
--   Within the same specificity, lower `priority` wins.
-- ---------------------------------------------------------------------------

create table routing_rules (
  id                     uuid primary key default gen_random_uuid(),
  priority               int not null default 100,
  match_source_key       text,                    -- specific source override
  match_source_category  source_category,         -- category-level default
  action_type            action_type not null,
  default_classification exposure_classification not null default 'investigate',
  form_template_key      text,
  submission_method      submission_method,
  requires_notarization  boolean not null default false,
  requires_human_review  boolean not null default true,
  auto_submit_allowed    boolean not null default false,
  required_docs          text[] not null default '{}',
  legal_basis            text,                    -- statute cite / note (SME-verified)
  playbook               text,                    -- short worker instructions
  active                 boolean not null default true,
  created_at             timestamptz not null default now()
);

-- Resolve the single governing rule for each exposure. Deterministic; no LLM.
create view v_exposure_routing as
select distinct on (e.id)
  e.id as exposure_id,
  e.case_id,
  s.key      as source_key,
  s.category as source_category,
  r.id       as rule_id,
  r.action_type,
  r.default_classification,
  r.form_template_key,
  r.submission_method,
  r.requires_notarization,
  r.requires_human_review,
  r.auto_submit_allowed,
  r.required_docs,
  r.legal_basis,
  r.playbook
from exposures e
join sources s on s.id = e.source_id
join routing_rules r
  on r.active
 and (r.match_source_key = s.key or r.match_source_key is null)
 and (r.match_source_category = s.category or r.match_source_category is null)
order by
  e.id,
  (r.match_source_key is not null) desc,        -- prefer exact source match
  (r.match_source_category is not null) desc,   -- then category match
  r.priority asc;                               -- then lowest priority number

-- Worker dashboard: follow-ups due within 7 days, not yet terminal.
create view v_followups as
select a.*, e.case_id, s.name as source_name
from actions a
join exposures e on e.id = a.exposure_id
join sources s   on s.id = e.source_id
where a.next_follow_up is not null
  and a.next_follow_up <= (current_date + 7)
  and a.status not in ('removed','not_removable','no_action','rejected');

-- Case rollup: counts by source category and status.
create view v_case_dashboard as
select
  e.case_id,
  s.category as source_category,
  e.status,
  count(*) as n
from exposures e
join sources s on s.id = e.source_id
group by e.case_id, s.category, e.status;
