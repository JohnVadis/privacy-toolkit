-- =============================================================================
-- Florida seed — exemptions, sources, routing rules
-- Depends on: schema.sql
-- Draft v0.1
--
-- IMPORTANT — legal_basis + eligibility:
--   Statute cites below are anchors for the workflow, NOT legal advice, and NOT a
--   substitute for eligibility review. Florida redaction/confidentiality is
--   county-level: each Clerk of the Circuit Court & Comptroller and each Property
--   Appraiser publishes its OWN form under ch. 119, F.S., and most must be
--   NOTARIZED and filed PER PERSON. Confirm the exact subsection and the county's
--   current form with the SME (the client) before any submission. Every rule below
--   that asserts an exemption is gated by requires_human_review = true.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Counties (subset — add the rest of FL's 67 as coverage expands)
-- ---------------------------------------------------------------------------
insert into counties (state_code, name) values
  ('FL','Hillsborough'),
  ('FL','Pinellas'),
  ('FL','Pasco'),
  ('FL','Polk'),
  ('FL','Orange'),
  ('FL','Miami-Dade'),
  ('FL','Palm Beach'),
  ('FL','Broward')
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Exemptions catalog (Florida)
-- ---------------------------------------------------------------------------
insert into exemptions
  (state_code, code, title, statute_cite, description, requires_notarization, requires_supporting_doc, covers_family)
values
  ('FL','FL_119_071_4d',
   'Protected professions personnel (and family)',
   's. 119.071(4)(d), F.S.',
   'Home address, telephone number, DOB, and photographs of listed current/former personnel (law enforcement, judges, prosecutors, code enforcement, firefighters, and others enumerated), plus—via separate forms—spouse and children. This is the most common clerk redaction basis.',
   true, true, true),

  ('FL','FL_119_071_2j',
   'Victim of violent crime / stalking',
   's. 119.071(2)(j), F.S.',
   'Personal information of victims of certain violent crimes may be removed from official records on request.',
   true, true, false),

  ('FL','FL_741_465_ACP',
   'Address Confidentiality Program (DV/stalking)',
   's. 741.465, F.S.',
   'Statewide Address Confidentiality Program administered by the Attorney General for victims of domestic violence/stalking. Enrollment is separate from a county clerk redaction and provides a substitute mailing address.',
   true, true, false),

  ('FL','FL_744_21031',
   'Guardianship-related exempt information',
   's. 744.21031, F.S.',
   'Companion basis that appears alongside 119.071 on many county redaction forms.',
   true, true, false),

  ('FL','FL_service_member',
   'Active service member confidentiality (Property Appraiser)',
   's. 119.071(4)(d), F.S. (verify county basis)',
   'Property Appraiser confidentiality for active-duty service members and other protected categories. Distinct process and form from the Clerk redaction.',
   true, true, true)
on conflict (state_code, code) do nothing;

-- ---------------------------------------------------------------------------
-- Sources (Florida gov + common commercial)
-- ---------------------------------------------------------------------------
insert into sources (key, name, category, jurisdiction_state, county_scoped, opt_out_url, notes) values
  ('fl_clerk_official_records', 'FL Clerk of Court — Official Records', 'gov_clerk', 'FL', true,  null,
   'Per-county Official Records search. Redaction via county "Request for Redaction of Exempt Personal Information from Non-Judicial Public Records" form; notarized; per person.'),
  ('fl_property_appraiser',     'FL Property Appraiser',                'gov_appraiser','FL', true, null,
   'Per-county confidentiality request. Separate from Clerk redaction. Service-member and protected-category forms vary by county.'),
  ('fl_sunbiz',                 'FL Division of Corporations (Sunbiz)',  'gov_corporate','FL', false, 'https://dos.fl.gov/sunbiz/',
   'Business filings expose principal/registered-agent addresses. Remedy is a RECORD CHANGE (amend filing + registered-agent/address service), not a removal.'),
  ('fl_dmv_flhsmv',             'FL Highway Safety & Motor Vehicles',    'gov_dmv','FL', false, null,
   'FLHSMV "Request to Withhold Personal Information" (form 96020) for eligible public officials / LE personnel and family.'),
  ('zillow',                    'Zillow',                                'property_site', null, false, 'https://www.zillow.com/z/privacy/',
   'Owner privacy / info removal request per listing.'),
  ('realtor_com',               'Realtor.com',                           'property_site', null, false, null, null),
  ('movoto',                    'Movoto',                                'property_site', null, false, null, null),
  ('fastbackgroundcheck',       'FastBackgroundCheck',                   'data_broker', null, false, 'https://www.fastbackgroundcheck.com/removal',
   'People-search opt-out; supports authorized-agent submissions. Prefer routing broker opt-outs through the removal-engine API.'),
  ('google_search',             'Google Search',                         'search_engine', null, false, 'https://support.google.com/websearch/answer/12719076',
   'Google indexes the source. Deindex only AFTER the underlying source is removed/changed; use "Results about you" / URL removal for residual PII.'),
  ('linkedin',                  'LinkedIn',                              'social_professional', null, false, null,
   'Legitimate professional profile — default KEEP unless client requests otherwise.')
on conflict (key) do nothing;

-- ---------------------------------------------------------------------------
-- Routing rules (Florida)
--   Specific source_key rules use low priority numbers so they win over the
--   category defaults. Category defaults sit at 100. Catch-all at 999.
-- ---------------------------------------------------------------------------
insert into routing_rules
  (priority, match_source_key, match_source_category, action_type, default_classification,
   form_template_key, submission_method, requires_notarization, requires_human_review,
   auto_submit_allowed, required_docs, legal_basis, playbook)
values
  -- Government: Clerk of Court redaction ------------------------------------
  (100, null, 'gov_clerk', 'redaction_request', 'remove',
   'fl_clerk_redaction_119071', 'mail', true, true, false,
   '{authorization,exemption_proof,notarization}',
   's. 119.071, F.S. (subsection per claimed exemption — SME verify)',
   'Identify each document (instrument #, book/page). Confirm claimed exemption is human_verified. Generate the county''s redaction form, one per person, notarize, submit to that county Clerk. Record confirmation ref; follow up in ~14 days.'),

  -- Government: Property Appraiser confidentiality --------------------------
  (100, null, 'gov_appraiser', 'confidentiality_request', 'remove',
   'fl_appraiser_confidentiality', 'mail', true, true, false,
   '{authorization,exemption_proof}',
   's. 119.071(4)(d), F.S. (county form — SME verify)',
   'Pull the specific county Property Appraiser confidentiality form (service-member vs. other protected category differ). Attach eligibility proof. Distinct from Clerk redaction — track separately.'),

  -- Government: Sunbiz corporate record change -----------------------------
  (100, null, 'gov_corporate', 'record_change', 'investigate',
   'sunbiz_amendment_ra', 'online_portal', false, true, false,
   '{}',
   'ch. 607/605, F.S. — public filing, not a ch.119 removal',
   'Inspect the filing for fields exposing a residential address (principal, mailing, registered agent). Where legally changeable, move to a registered-agent + business-address service (e.g., Northwest Registered Agent) and amend the annual report. This CHANGES the source record rather than deleting it.'),

  -- Government: FLHSMV / DMV withhold --------------------------------------
  (100, null, 'gov_dmv', 'confidentiality_request', 'remove',
   'flhsmv_withhold_96020', 'email', false, true, false,
   '{authorization,exemption_proof}',
   's. 119.071, F.S. (FLHSMV form 96020)',
   'Complete FLHSMV form 96020 with eligibility documentation; submit to FLHSMV public-official block.'),

  -- Property sites: Zillow-specific, then generic --------------------------
  (50, 'zillow', null, 'opt_out', 'suppress',
   null, 'online_portal', false, true, false,
   '{}', null,
   'Submit Zillow owner privacy / info-removal request for the specific listing URL. Verify the listing is the client''s residence first.'),

  (100, null, 'property_site', 'opt_out', 'suppress',
   null, 'online_portal', false, true, false,
   '{}', null,
   'Use the site''s own privacy/removal mechanism per listing URL. Remove the SOURCE before deindexing from Google.'),

  -- Data brokers: route to removal-engine API -----------------------------
  (100, null, 'data_broker', 'opt_out', 'remove',
   null, 'api', false, false, true,
   '{}', null,
   'Do NOT hand-build per-broker scrapers. Submit through the removal-engine API (e.g., Optery custom removal); poll webhook for status; reconcile back to this exposure. Auto-submit allowed because the vendor performs it under its own ToS handling.'),

  -- Search engines: deindex only after source handled ---------------------
  (100, null, 'search_engine', 'deindex_request', 'suppress',
   null, 'online_portal', false, true, false,
   '{}', null,
   'Only after the linked source exposure is removed/changed. Use Google "Results about you" / outdated-content + URL removal for residual PII. Do not deindex live sources.'),

  -- Social/professional: keep by default ----------------------------------
  (100, null, 'social_professional', 'keep', 'keep',
   null, null, false, false, false,
   '{}', null,
   'Legitimate professional presence. No action unless the client specifically asks to suppress.'),

  -- Catch-all --------------------------------------------------------------
  (999, null, 'other', 'opt_out', 'unclassified',
   null, 'manual', false, true, false,
   '{}', null,
   'No rule matched — send to human review for classification and workflow assignment.');
