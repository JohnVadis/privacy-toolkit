#!/usr/bin/env python3
"""
scaffold_mapping.py — start a mapping for a NEW county form. Run ONCE per form.

    python scaffold_mapping.py forms/_blank_pdfs/hillsborough_redaction.pdf fl_clerk_redaction_hillsborough

It lists the form's fillable fields and writes a starter file to
forms/mappings/<form_key>.map.yaml with every field stubbed. You then edit that
file once — point each field at a client variable (from:) or a constant
(literal:), and set checkbox rules. After that, fill_forms.py reuses it for every
client with no further work.

Available client variables (see client_context.py):
  full_name first_name middle_name last_name suffix dob phone email
  home_line1 home_line2 home_city home_county home_state home_zip home_full
  case_number case_display_name agent_name agent_email
  exemption_code exemption_title exemption_cite today
"""
from __future__ import annotations
import os, sys
import pdf_fill

VARS_HINT = ("full_name first_name middle_name last_name suffix dob phone email "
             "home_line1 home_line2 home_city home_county home_state home_zip home_full "
             "case_number case_display_name agent_name agent_email "
             "exemption_code exemption_title exemption_cite today")


def yaml_escape(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def main():
    if len(sys.argv) != 3:
        print("Usage: python scaffold_mapping.py <blank_form.pdf> <form_key>")
        sys.exit(1)
    pdf_path, form_key = sys.argv[1], sys.argv[2]
    fields = pdf_fill.list_fields(pdf_path)
    if not fields:
        print("This PDF has no fillable fields. It must be filled by overlay instead "
              "(the README explains that path). Nothing scaffolded.")
        sys.exit(1)

    out = os.path.join("forms", "mappings", f"{form_key}.map.yaml")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    lines = [
        f"# Mapping for '{form_key}'. Edit each field ONCE, then reuse for every client.",
        f"# Variables you can use with `from:` -> {VARS_HINT}",
        f"form_key: {form_key}",
        f"pdf: {pdf_path}",
        "fields:",
    ]
    for f in fields:
        fid = yaml_escape(f["id"])
        if f["type"] == "checkbox":
            lines.append(f"  {fid}:  # checkbox (on={f.get('on_value')}). "
                         f"Use {{checkbox: true, check: true}} or {{checkbox: true, when: {{field: exemption_code, equals: FL_119_071_4d}}}}")
            lines.append(f"    {{ checkbox: true, check: false }}")
        else:
            lines.append(f"  {fid}:  # {f['type']} — set `from:` a variable or `literal:` a constant")
            lines.append(f"    {{ from: \"\" }}")
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"Wrote {out} with {len(fields)} field(s). Open it and fill in the `from:`/`literal:` values.")


if __name__ == "__main__":
    main()
