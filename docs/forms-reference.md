# Forms reference (Hillsborough County, FL)

All four are flat scans filled via overlay mode. Each mapping lives in
`forms/mappings/`. "Left blank" means intentionally not filled (signed/notarized/
completed by hand or on-site).

## hillsborough_appraiser_confidentiality
Property Appraiser — "Request of Confidentiality" (s. 119.071(4)(d)). One page, has a
text layer. The category list on this form is informational bullets, so **no category
checkbox** — text fields only.
- Filled: print name, Property ID (`home_parcel_id`), Name of Owner / Relationship,
  Home Address, Home phone.
- Left blank: Office of Employment, Badge/Cert #, Supervisor, Signature, Date.

## hillsborough_appraiser_military
Property Appraiser — "Request for Confidentiality – Service Member" (s. 119.071(5)).
One page, **image-only scan** → absolute coordinates.
- Filled: print name, Property ID, Name of Owner / Relationship, Home Address.
- Status checkbox by role: `primary`→member, `spouse`→spouse, `child`/`dependent`→dependent.
- Left blank: evidence boxes (Military ID / Orders / DD-214 — worker checks what's
  attached), Signature, Date.

## hillsborough_clerk_redaction
Clerk of Court — "Request for Redaction of Exempt Personal Information from Non-Judicial
Public Records." Four pages, text layer. Checkbox glyphs: `\uf072` (p1), `\uf0a8` (p2).
- **p1 requestor type** by role: `primary`→current/former employee, `spouse`→spouse,
  `child`→child.
- **p1 statutory category** by `exemption_category`:
  | category | box (x, y) |
  |---|---|
  | law_enforcement | 55, 564 |
  | judges | 55, 349 |
  | military | 312, 215 |
- **p2 contact**: Printed Name, Telephone, Email.
- **p2 information to be redacted**: Address (73,601), Telephone (73,487), DOB box
  (289,471) checked + DOB value written (365,469). SSN box left unchecked by design.
- **p4**: notary County (`home_county`), Job Title, Employing Agency.
- **p2 documents to be redacted** — a `tables:` block over 4 ruled lines (baselines
  144.9, 127.5, 110.0, 92.6; columns at x 74 / 227 / 299 / 371). Rows come from the
  client's `documents:` list where `list: redact`. More than 4 and the last line reads
  "Continued on Attachment A", with every document on the appended sheet.
- **p3 release of prior redactions** — the same columns over 2 ruled lines (baselines
  415.8, 398.4), from rows with `list: release`, overflowing to Attachment B.
- Left blank: Signature/Date and the notary sub-fields. With no documents imported the
  tables print blank too — the fallback is still to write them in at the counter.

## fl_dos_public_records_exemption
FL Department of State — "Public Records Exemption Request" (Rev 07/2025). Four pages,
text layer. Checkboxes are **vector squares** (not glyphs). Filename says
"address-confidentiality" but this is the DoS exemption form.
- **p1 attestation** by role (defaults to *current* — verify current vs former):
  `primary`→current (190,475), `spouse`→spouse-of-current (191,454), `child`→child-of-current (191,431).
- **statutory category** by `exemption_category`:
  | category | page | box (x, y) |
  |---|---|---|
  | judges | 1 | 328, 106 |
  | law_enforcement | 2 | 48, 678 |
  | military | 2 | 325, 636 |
- **p2 identity**: Printed Name (96,469), DOB (327,469), Phone (460,469),
  Home Address (101,429), notary County.
- Left blank: Signature/Date; the **Division of Corporations addendum (p3)** unless the
  client has Sunbiz records exposing the home address (then it needs an alternate
  address — see `docs/decisions.md` D2 on the registered-agent strategy); p4 is only
  for congressional members/public officers.

## Coordinate note
All coordinates are PDF points, origin bottom-left, at the form's native page size.
They were located with pdfplumber and verified by rendering. If a county reissues a
form with a shifted layout, re-run the coordinate steps in `docs/adding-forms.md`.
