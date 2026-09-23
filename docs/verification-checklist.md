# First real-use verification checklist

Run this the first time a form is used for an actual client, and any time a county
reissues a form. Ten minutes now prevents a rejected filing later.

## Every form
- [ ] Client file (`clients/<name>.yaml`) is complete: legal name, DOB, residence with
      **county**, phone, email, and `exemption.category`.
- [ ] `exemption.category` matches how the client actually qualifies
      (`military` | `law_enforcement` | `judges`). Eligibility confirmed by a human.
- [ ] Generate: `python fill_forms.py --client clients/<name>.yaml`
- [ ] Render each output and eyeball it:
      `pdftoppm -png -r 100 output/<slug>/<form>__primary.pdf /tmp/chk`
- [ ] Every value sits on its line; every mark sits in its box (not the neighbor).
- [ ] Nothing that should be blank got filled, and vice-versa.

## Clerk redaction (4 pp)
- [ ] p1: correct requestor-type box (role) and correct statutory-category box.
- [ ] p2: Address / Telephone / DOB boxes checked, SSN box NOT checked, DOB value present.
- [ ] p4: notary county, job title, employing agency present; Signature/Date blank.
- [ ] The record search was run with `%` on the name (`MARLOWE%`, not `MARLOWE`) and
      repeated for every name the client has held, including maiden and prior married
      names. See the README — the unwildcarded search missed 89% of one real index.
- [ ] The exported CSV's row count matches the result count the search page reported.
- [ ] p2 documents table: every imported row on its ruled line, in the right column,
      and the instrument/book/page matching the Official Records search they came from.
      Nothing imported? The table is blank and gets completed at the Clerk's office
      (419 Pierce St, Rm 140).
- [ ] More documents than lines: the last line reads "Continued on Attachment A", the
      attachment lists **all** of them, and the requestor initials and dates each
      attachment page. Same for Attachment B if prior redactions are being released.
- [ ] No document title printed cut short on the form
      (`python validate_client.py --client clients/<name>.yaml` warns when one is).

## DoS exemption (4 pp)
- [ ] p1: attestation box correct — confirm **current vs former** (defaults to current).
- [ ] Category box correct (judges on p1; law enforcement / military on p2).
- [ ] p2: identity fields + notary county present.
- [ ] Division of Corporations addendum (p3): filled only if the client has Sunbiz
      records exposing the home address.

## Service-member confidentiality
- [ ] Status box matches the person (member / spouse / dependent).
- [ ] Evidence box(es) checked by hand for whatever is actually attached
      (Military ID / Orders / DD-214).

## Before submitting (human, always)
- [ ] Notarize forms that require it (both 4-page forms do).
- [ ] Client (or authorized agent) signs and dates.
- [ ] Submit per the form's instructions; record the confirmation/reference number.
- [ ] Set a follow-up to re-check the record after the agency's processing window.

## Per statutory category
The category boxes on the two 4-page forms sit close together. Before the client's
first filing under a category you haven't rendered before, generate one and confirm the
right box is marked.

**All three categories are now verified on BOTH 4-page forms.** On the DoS form
(verified 2026-09-21 by rendering each category and reading the label beside the mark):

| Category | Box it marks on the DoS form |
|---|---|
| `law_enforcement` | p2 — "Law enforcement personnel including correctional officers and correctional probation officers (s. 119.071(4)(d)2.a)" |
| `military` | p2 — "Person employed by the United States Department of Defense … or who is a servicemember of a …" |
| `judges` | p1 — "Judge - district court of appeal, circuit court and county court, or justice of the Florida Supreme Court (s. 119.071(4)(d)2.e)" |

Method, if a form is ever reissued and this needs redoing: generate the same client
under each category, render the pages, and crop a band around the mark wide enough to
read the label next to it. Diffing a no-category render against a marked one locates
the mark's exact coordinate if it has drifted.
