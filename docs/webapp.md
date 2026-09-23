# The local web app

A desktop application for managing clients and generating forms without hand-editing
YAML. It is a **wrapper over the existing engine**, not a second implementation: every
fill goes through the same `fill_forms.fill_client()` the CLI calls, and a client
created in the UI is an ordinary `clients/<slug>.yaml`.

The UI is HTML, so there is still an HTTP server behind the window — but it is private
by construction, not merely unexposed. See **Why this is not "hosting"** below.

## Running it

For a non-technical worker: **double-click the `Privacy Toolkit` icon** on the Desktop.
That icon runs `pythonw.exe -m webapp.desktop`, so there is no console window at all —
just the app, in about two seconds.

The icon is created by `Start Privacy Toolkit.bat`, which is the first-run and repair
path. It is idempotent and safe to open any number of times.

- First run: creates `.venv`, installs `requirements.txt`, records a SHA-256 of that
  file in `.venv/.requirements-installed`. ~40 seconds, and the only step that needs
  an internet connection.
- Later runs: compares that hash, skips the install when nothing changed, and starts
  in about a second. Editing `requirements.txt` triggers exactly one reinstall.
- It then writes `Privacy Toolkit.lnk` to the Desktop and the Start menu, pointing at
  `pythonw.exe -m webapp.desktop` with `webapp/static/toolkit.ico`, and opens the app.
- Failure paths print what to do: Python missing, venv creation failed, install failed
  (offline / proxy), window failed to open. Each pauses so the message is readable
  after a double-click.
- Because the shortcut uses `pythonw.exe` there is no console to print to, so
  `desktop.py` reports any startup failure in a **message box** pointing back at the
  .bat. A silent no-op would be the worst outcome for a non-technical user.

From a terminal:

```bash
pip install -r requirements.txt
python -m webapp.desktop                  # the app window
python -m webapp.main --open-browser      # browser instead, if you prefer
python -m webapp.main --port 8000         # pin the port (default: OS picks one)
```

The browser forms print a URL containing the session key. That link *is* the
credential — it is what gets you past the gate below.

**Loopback only.** `webapp.main.bind()` hardcodes `127.0.0.1`. Binding to `0.0.0.0`
would expose every client file on the machine to the network. If you ever need remote
access, put it behind an authenticated tunnel; don't change the bind address.

## Why this is not "hosting"

A loopback server is still reachable by everything else on the machine, including web
pages the worker has open. Before `webapp/security.py` existed, this was demonstrably
exploitable: a request with `Host: evil.example.com` was served 200, and a cross-site
form POST to `/clients/new` returned 303 and **created a client file**. Same-origin
policy does not block form submissions, only reading the response.

Three checks now run in front of every request and every static file:

1. **Host** — the hostname must be `127.0.0.1` or `localhost`. Defeats DNS rebinding.
2. **Cross-site** — `Sec-Fetch-Site` must be `same-origin` or `none`. `cross-site` and
   `same-site` are refused, which is what stops a malicious page's form POST. Clients
   that send no `Sec-Fetch-Site` (curl, scripts) fall back to an `Origin` check and are
   still gated by the token.
3. **Token** — a `secrets.token_urlsafe(32)` minted per process. The window opens
   `?k=…` once; that sets an HttpOnly, SameSite=Strict cookie and redirects to a clean
   URL, so the key never sits in the address bar or a Referer. Another local process
   cannot guess it, and restarting invalidates every old URL.

Plus: the port is **OS-assigned**, not a fixed 8000, so there is nothing to guess;
`SO_REUSEADDR` is deliberately not set (on Windows it would let another process bind
underneath); and closing the window exits the process, so no server is left running
unnoticed.

`/healthz` is exempt from the token so a launcher can poll readiness. It returns the
literal string `ok` and nothing else.

This is not a login — there are no users and no accounts. It exists so that "listening
on loopback" does not mean "anything on this box can drive the app".

### Verified, not assumed
`sec_test.py` in the scratchpad re-runs the original attacks against the hardened
server. All six are refused with 403 — foreign Host, cross-site POST with and without
`Sec-Fetch-Site`, local process with no token (GET and POST), and a wrong token — and
no file is created. The legitimate path still returns 200 on every route, and the
desktop app's generated PDFs are byte-identical to the CLI's.

## What it will not do

These are enforced in code, not just intended (see the module docstring in
`webapp/main.py`):

| Rule | How it holds |
|---|---|
| No auto-submission of government forms | There is no submit route, and no HTTP client (`requests`, `httpx`, …) is imported anywhere under `webapp/`. The only outputs are a preview and a download. |
| Opt-out sites are prepared, not submitted | `/worklist` builds the spreadsheet and renders `sites.yaml` read-only. Site links are ordinary `target="_blank"` anchors the worker clicks. The app never requests them. |
| No eligibility logic | `exemption.category` is a plain dropdown. Validation checks *presence*, never *fitness*. |
| No model calls | The request path imports only the engine modules. |
| No PII in logs | The engine takes a `report` callback: the CLI passes `print()`, the web app passes a per-request collector that is rendered once on the page. |
| No external network calls | htmx and the CSS are vendored under `webapp/static/`. No CDN links, no webfonts. |
| Path safety | Every `{slug}` and filename is checked against a strict pattern *and* re-checked for containment under `clients/` / `output/` after resolution. |
| Not reachable by anything else | OS-assigned loopback port, Host validation, cross-site rejection, per-process token. See **Why this is not "hosting"**. |

## The reuse boundary

```
            CLI                         web app
   fill_forms.main()            webapp/routers/generate.py
   build_worklist.main()        webapp/routers/clients.py
   validate_client.main()       webapp/routers/forms.py
            │                            │
            └──────────┬─────────────────┘
                       ▼
        fill_forms.fill_client() / fill_one()
        build_worklist.build_worklist()
        client_context.load_client() / build_context() / save_client()
        validate_client.validate_client()
                       ▼
            pdf_fill.py / pdf_overlay.py
```

Everything above the fold is a front end. Everything below is the engine, unchanged in
behaviour — a refactor moved logic out of `main()` into importable functions, and the
CLI still calls them through `if __name__ == "__main__"`.

**The web app contains no fill logic.** If you find yourself writing a coordinate, a
field name or a value lookup under `webapp/`, it belongs in a mapping or the engine
instead.

### What the web app adds on its own
- `webapp/formparse.py` — flat HTML form keys (`persons[0][addresses][1][city]`) into
  the nested client model. Indices may be sparse; lists are rebuilt in numeric order.
- `webapp/model.py` — builds a client dict in `clients/_TEMPLATE.yaml` key order.
- `webapp/deps.py` — slug/filename guards, templates, the `report` collector.
- `webapp/security.py` — the Host / cross-site / token gate.
- `webapp/desktop.py` — the pywebview window; runs the server on a background thread
  because pywebview must own the main thread.

## Routes

| Method | Path | Name | What it does |
|---|---|---|---|
| GET | `/` | `client_list` | Every `clients/*.yaml` except `_TEMPLATE`, with county, category, person count and a validation status. |
| GET | `/clients/new` | `client_new` | Empty client form. |
| POST | `/clients/new` | `client_create` | Validates, then writes `clients/<slug>.yaml`. Blocks on a name collision. |
| GET | `/clients/{slug}/edit` | `client_edit` | The client form, populated. |
| POST | `/clients/{slug}/edit` | `client_update` | Validates, then overwrites the same file. |
| GET | `/clients/{slug}/delete` | `client_delete_confirm` | Confirmation page naming the client and every file that would go. Never deletes. |
| POST | `/clients/{slug}/delete` | `client_delete` | Moves the client file and `output/` folder into `trash/`. Requires `confirm=yes`. |
| GET | `/clients/fragments/person` | `fragment_person` | HTMX: one blank person block. |
| GET | `/clients/fragments/address` | `fragment_address` | HTMX: one blank address block. |
| GET | `/clients/fragments/contact` | `fragment_contact` | HTMX: one blank phone/email row. |
| GET | `/clients/{slug}/documents` | `documents_page` | The client's recorded-document list, plus the CSV import. |
| POST | `/clients/{slug}/documents/import` | `documents_import` | Parses a CSV/paste and shows it; writes only when sent again with `action=save`. |
| POST | `/clients/{slug}/documents` | `documents_save` | Saves the row edits: which list a row is on, and which rows to drop. |
| GET | `/clients/{slug}/generate` | `generate_page` | Person + form picker, with any pre-flight warnings. |
| POST | `/clients/{slug}/generate` | `generate_run` | Runs the engine; renders previews + downloads. |
| GET | `/clients/{slug}/files` | `client_files` | Every form ever generated for this client, newest first. |
| GET | `/clients/{slug}/view/{filename}` | `view_file` | Reads a filled PDF in the app — all pages, zoom, print. |
| GET | `/clients/{slug}/file/{filename}` | `inline_file` | The PDF served `inline`; what the viewer embeds. |
| GET | `/clients/{slug}/preview/{filename}` | `preview_file` | Page 1 as a PNG, rendered **in memory**. |
| GET | `/clients/{slug}/download/{filename}` | `download_file` | The file as an attachment, `Cache-Control: no-store`. |
| POST | `/clients/{slug}/reveal/{filename}` | `reveal_file` | Opens the folder with the file selected. |
| GET | `/clients/{slug}/worklist` | `worklist_page` | Build form + read-only `sites.yaml` table. |
| POST | `/clients/{slug}/worklist` | `worklist_run` | Builds `opt_out_worklist.xlsx`. |
| GET | `/forms` | `forms_overview` | Read-only mapping list: county, mode, category coverage. |
| GET | `/healthz` | `healthz` | Liveness, for scripts. |

The interactive API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled. This is a
local tool, not a service.

## Importing documents, and picking which ones

`/clients/{slug}/documents` is where a Clerk record list becomes the rows the redaction
form prints. It is two steps on purpose.

**Step 1 — read.** The CSV (or paste, or upload) is parsed and every row is listed with
a tick box, unticked. Nothing is written. A name search returns every SMITH in the
county, so the table also shows the **party names** and a filter box: type a name,
press *Tick shown*, and only the visible rows are ticked. The count reports the whole
set ("6 of 237 ticked · 12 shown") rather than what happens to be on screen, so a
filter can never hide a row that is already ticked.

**Step 2 — import.** The form posts the same text back plus the ticked positions. The
server re-parses and keeps those rows. Nothing is held between the two requests:
there is no session, and the party names used to choose are not something to leave
lying around.

Two properties worth keeping:

- **Party names are never stored.** `documents.normalize()` keeps `FIELDS` only, so
  `grantor`/`grantee` exist for the duration of the picker and no further. A client
  file records the documents, not who else was on them.
- **Nothing is ticked by default.** The form's own warning is that only the documents
  it identifies get redacted, which cuts both ways — a row nobody chose should not end
  up on it. Importing with none ticked is refused rather than treated as "all".

Rows already on the client's list are marked "already on file"; importing one again
updates it in place instead of duplicating it.

## Two slugs, don't confuse them

- **URL slug** = the client file's stem. `clients/Jane D.yaml` → `Jane D`, which
  appears in URLs percent-encoded as `Jane%20D`.
- **Output slug** = `slugify(case.display_name)`, which names `output/<slug>/`. For
  that same file it is `doe_jane`.

Saving never renames the file, even when the display name changes — renaming would
break links and orphan the previously generated output folder. The output *folder*
does follow the display name, because that is how the CLI has always behaved.

## Known behaviour: editing strips YAML comments

Saving a client through the UI rewrites the file with `yaml.safe_dump`, so comments in
that file are lost. Values, key order and types are preserved (a round-trip of the
rendered form produces an identical parse). This is deliberate — round-tripping
comments would mean adding `ruamel.yaml`.

It matters for exactly one file: `clients/example_client.yaml`, whose comments are
documentation. Don't save that one from the UI, or restore it from version control
afterwards. Real client files carry no comments worth keeping.

## Viewing filled forms

`fill_forms.generated_files(client)` reads `output/<slug>/` and returns what is there,
newest first, splitting `<form_key>__<person>.pdf` via `split_output_name()`. There is
no index or database: **the files on disk are the record**, so forms filled from the
CLI appear in the app too.

The client list shows a "Filled forms" count linking to `/clients/{slug}/files`, and
that page offers three things per file:

- **View** — `/clients/{slug}/view/{filename}` embeds `/clients/{slug}/file/{filename}`
  (served `inline`) in an `<object>`. WebView2 and every browser have a built-in PDF
  reader, so paging, zoom and print come free. A fallback download link shows if the
  reader is unavailable.
- **Save a copy** — the attachment download.
- **Show in folder** — the files are already on this machine, so for a worker who
  needs to attach one to an email or print it, opening the folder beats re-saving.

### Two Windows gotchas, both found by testing
1. **pywebview cancels downloads by default.** With `ALLOW_DOWNLOADS` unset, its
   EdgeChromium backend runs `args.Cancel = True` on every download — the button did
   nothing, with no error, and it worked in a browser, which made it easy to miss.
   `desktop.py` now sets `webview.settings['ALLOW_DOWNLOADS'] = True`, which gives the
   normal Windows "Save As" dialog.
2. **Explorer's `/select,` needs the path quoted.** Passing it as a list argument
   (`["explorer", "/select," + path]`) gets quoted in a way Explorer won't parse, and
   with a space anywhere in the path it silently opens Documents instead. The route
   uses a single command-line string, `explorer /select,"<path>"`. No shell is
   involved and the path has already passed the output-folder guard.

## Two kinds of edit

A mapping entry and a manual correction are the same thing — text or an X at a
coordinate on a page — at different scopes, so one click-to-place UI serves both.

| | Scope | Stored in | Survives regeneration |
|---|---|---|---|
| Mapping entry | every client, that form | `forms/mappings/<key>.map.yaml` | n/a |
| Correction | one client, one form | `clients/<slug>.yaml` under `corrections:` | **yes** |

### Editing a form in place
`/clients/{slug}/text/{filename}` draws the form and puts a control over every spot the
engine writes to: an editable box on each value, a real tick box on each mark. Type on
the form, click a box to tick or untick it, click an empty spot to add text or a tick
there, then Save once — it commits every page, not just the one on screen.

The background is the **blank** page. Using the filled page would leave the original
text showing underneath every input.

`fill_forms.form_layout()` produces the controls. Anchored entries go through
`pdf_overlay.entry_position()` — extracted from `resolve_entries` for this — so an
anchor-positioned control sits exactly where its value will be drawn, not at a guess.

Three things can be stored, and each is only written when it disagrees with the
mapping, so the mapping keeps driving everything untouched:

| Stored | What it does | Applied |
|---|---|---|
| `overrides` | replaces what an entry writes | during the fill |
| `marks` | forces a tick box on or off | during the fill |
| `corrections` | adds text or a tick at a free coordinate | after the fill |

Clearing a box back to its original text removes the override.

An edit is an **override**, and it is applied *while filling*, not stamped afterwards —
`_fill_overlay` / `_fill_fillable` consult `_overrides_for()` in place of the resolved
value. It has to work that way: stamping replacement text on top of the original would
simply overlap and be unreadable.

Stored on the client as:

```yaml
overrides:
  hillsborough_clerk_redaction:
    - { entry: 6, from: full_name, text: "Doe, Jane R.", person: primary }
```

`entry` is the entry's position for an overlay mapping, or the field name for a
fillable one. `from:` is stored alongside and re-checked at fill time: if a later
mapping edit reorders the entries so that position no longer holds `full_name`, the
override is skipped with a warning rather than silently landing on a different field.

### Adding text where the form has none
Some fields are deliberately left blank by every mapping — signature lines, the Clerk
form's instrument/book/page table, the evidence boxes. There is no existing value to
edit, so `/clients/{slug}/correct/{filename}` shows the generated page, clickable. A click is
converted to a PDF coordinate in the browser (the page's size in points rides along as
`X-Page-Width-Pt` / `X-Page-Height-Pt` headers on the PNG), you type the value, and it
is appended to `corrections[form_key]` on the client and the form is re-generated.

`fill_forms.apply_corrections()` stamps them **after** the mapping, so a correction
always wins, and it runs on every generation. Annotating the output PDF instead would
be silently undone by the next Generate — which is when you would least notice.

Two consequences worth knowing:
- `webapp/model.py` lists `corrections` in `UNMANAGED_KEYS` and `preserve_unmanaged()`
  copies it forward on save. The edit form rebuilds the client from scratch, so without
  that, saving the client would wipe every correction.
- A correction carries `person:` when the file it was made on belongs to one, so a
  spouse's fix does not land on the primary's copy.

### Adding a form
**Forms → Add a form** takes a blank PDF and proposes a mapping (`form_import.py`, also
a CLI). What it can do depends entirely on the PDF:

| The PDF | What happens | How good |
|---|---|---|
| has AcroForm fields | `fillable` mapping, one entry per field, matched to a client variable by name | reliable — the PDF already says where everything is |
| flat, with a text layer | `overlay` mapping, each value anchored to the label it belongs to | good for text, **no checkboxes at all** |
| an image, no text | empty mapping | nothing detectable; place everything by hand |

Measured against the hand-written mappings: Appraiser confidentiality 5 of 5 text
values, DoS 4 of 5, Clerk 5 of 7 — and **0 of the 15 checkboxes across them**. On these
forms the boxes are neither text glyphs nor rectangles, so there is nothing to find.
The importer says so rather than quietly producing a form with no ticks.

An upload is judged on its **contents** (`%PDF` magic bytes, non-empty, under 40 MB),
never on its file name. An earlier version also checked the name against a character
whitelist, which refused perfectly good files like
`Clerk Redaction (rev 2025) [FINAL].pdf` and made people rename things for nothing —
the uploaded name is never used as a path, only as a fallback short name, and that is
slugified anyway.

Every rejection hands back what was typed. A browser cannot refill a file input, so the
page says to choose the PDF again and keeps the short name, county and title.

Two things learned building it, both now guarded:
- The fill-in rule before a label (`______`) extracts as a word. Stripping every
  non-alphanumeric "word" also ate the `/` in `Name of Owner / Relationship:`, producing
  an anchor that matched nothing. `_is_rule()` now needs 3+ rule characters.
- Deduping label matches per page kept a second `Email:` from the county's own contact
  block on the last page, which would have stamped the client's address into the
  agency's details. Dedupe is document-wide.

There is deliberately no rule for a bare `Date:`: on these forms that is the date the
client signs in front of a notary, and pre-filling it would date a sworn document.

### The layout editor
`/forms/{form_key}/edit` is a visual editor, not a YAML box with a picture next to it.
Every entry is a **marker** on the blank page — a round dot for a value, a square for a
tick box — with its name on a tag beside it so the label never covers the point it
describes.

- **Click** selects; the panel shows what it writes, where, and on which page.
- **Drag** moves it, keeping the grab offset. A drag only starts after 3px of travel,
  so selecting something doesn't nudge it — an earlier version moved a marker the
  moment you clicked it, which meant looking at a form changed it.
- **Arrow keys** nudge the selected marker 1 pt, or 10 pt with Shift. This is how you
  land on a line exactly; dragging gets you close, keys get you right.
- **Delete** removes it, **Esc** deselects.
- **+ Value / + Tick box** then a click on the page adds one.
- The panel edits meaning too: which client detail, fixed text, or the condition that
  turns a tick on — no YAML needed for anything the panel covers.

**A marker is the box the value will fill, not a dot near it.** reportlab draws from
the start of the BASELINE with glyphs going up and to the right, so a marker centred on
that point sat low and left of the text it stood for — for a long value like a full
name that was most of its width out. A marker's left edge and bottom edge now sit on the
draw point, and its width comes from `reportlab.pdfmetrics.stringWidth` using the same
font the engine draws with. Measured against the ink in a render: left edges within
1.5 pt (side-bearing), widths within 1 pt.

**The editor previews itself on open**, against `client_context.EXAMPLE_CLIENT` — a
fictional client with every variable filled and roughly realistic lengths. A blank page
cannot tell you whether a marker is on the right line; only a rendered value can. The
example client is never written to `clients/` and never used for a filing; it exists so
a layout can be checked without opening a real client's details. The picker offers it
first, with the real clients after.

**Preview is a working mode, not a dead end.** Markers stay on the page over a
preview — shrunk, naming themselves on hover — because the preview is exactly when you
can see something sitting in the wrong place, and being sent back to the blank form to
fix it was backwards. Drag or nudge while previewing and it re-renders after 500 ms of
quiet, so you watch the value land where you put it.

The preview endpoint takes the pending operations as well as the file text and applies
them before filling, so what you see is what Save would write — not the last version on
disk. A bad pending change is refused with a message instead of rendering something
misleading, and nothing is written either way.

Everything is queued as operations and sent in **one batch** on Save. Nothing is written
until then, and `beforeunload` asks before you lose pending work. `_apply_ops` decides
the order server-side — update and move existing entries first, then delete bottom-up,
then append — so browser-side ordering can't corrupt the file. A malformed batch is
refused whole; it never half-applies.

An anchored marker's x/y are read-only in the panel: they are derived from a label on
the page, and moving it stores an offset instead.

Static assets are served with `?v=<mtime>`. Without it the app can keep running a
cached copy of its own JS after an update, which looks exactly like a bug that won't die.

### Moving an entry
Every entry that resolves is drawn as a **draggable pin** on the blank page in the
mapping editor. Drag it, Save, done — this is the answer to an import putting something
in the wrong place.

- `move_entries`, `update_entries`, `remove_entries` and `add_entries` all edit the file
  **where the text sits** rather than re-dumping the parsed YAML, because re-dumping
  would strip the comments that explain what every coordinate is for. Verified on the
  Clerk mapping (16 entries, 20 comments): a batch that moves one, retargets two,
  deletes one and adds two keeps every comment except the deleted entry's own note.
- An entry's span covers its own lines only. These mappings put a group heading above
  the entries it describes, so a span running to the next `-` swallowed the heading
  belonging to the *following* entry — rewriting one entry then destroyed the next
  one's documentation.
- Rewriting an entry carries its trailing `# …` note across, since it usually says
  which line on the form the entry writes to and that stays true.
- An **absolute** entry gets new `x`/`y`. An **anchored** entry keeps its anchor and
  gets new `dx`/`dy`, computed from where its anchor actually sits, so it stays
  resilient to the county reflowing the form.
- Only entries you actually dragged are posted, so a save never rewrites the rest.
- If an anchor can't be found on the page the move is dropped rather than writing the
  absolute position into `dx`/`dy`, which would be nonsense.

The entry index travels as a *value* (`pos[n][entry]`), not as the group key —
`formparse` renumbers sparse numeric keys into a list, which would silently move a
different entry. That bug was live until testing caught it.

### Editing a mapping
`/forms/{form_key}/edit` puts the blank form beside the YAML:
- clicking the page yields a ready-to-paste `- { page: N, x: …, y: …, from: }`,
- **Preview with a client** fills the form with the *unsaved* text for a real client
  and renders it, in a temp folder that is deleted before the response returns,
- saving runs `mapping_edit.check_mapping()` first and refuses to write if anything is
  wrong, then copies the previous version to `forms/mappings/_backups/`.

`check_mapping()` catches broken YAML, an unknown or typo'd key, a coordinate off the
page, a page number the form doesn't have, an entry with no value, and a `form_key`
that disagrees with the filename. It is structural only — it cannot know a coordinate
is the *right* box. That is what the preview and
`docs/verification-checklist.md` are for. Same checks from a terminal:

```bash
python mapping_edit.py                    # check every mapping
python mapping_edit.py <form_key>         # check one
python pdf_render.py <file.pdf> 2 p2.png  # render a page to look at
```

## Sending a comment

**Comment** in the masthead photographs the screen you are on, lets you mark it up, and
opens an email to `feedback.FEEDBACK_TO` (currently john@qvadisai.com) with your note in
it. The app opens the mail client; **it never sends anything**, exactly like the opt-out
site links.

- The capture happens *before* navigating, via `fetch` on the Comment link, so you get
  the screen you were looking at rather than the comment page.
- `feedback.capture()` uses **PrintWindow with PW_RENDERFULLCONTENT**, which reads the
  window's own buffer. A screen grab was wrong: it captures whatever is on top of the
  app, and raising the window first doesn't help because Windows refuses a focus change
  asked for by a background process. There's a screen-grab fallback if PrintWindow ever
  returns nothing or a black frame.
- Two markup tools: **Highlight** (translucent yellow) and **Hide** (solid black).
  Hide is composited into the image data, so a redacted area is genuinely gone from the
  file that gets attached — not covered by an overlay someone could peel off.
- The picture and the note are written to `feedback/` (gitignored), the folder is
  opened, and the email body says which file to attach. **No mail URL scheme can attach
  a file** — not Gmail's, not `mailto:` — so that step is the user's, and the UI says so
  rather than silently dropping the screenshot.

### This is the one place data can leave the machine
A screenshot of this app is client PII, and mailing it sends it through Google. That is
why the Hide tool exists, why the page warns before you send, and why a human presses
send. Changing `FEEDBACK_TO` changes who receives client data.

## Deleting a client

`client_context.delete_client(slug)` **moves** the client file and their `output/`
folder into `trash/<YYYY-MM-DD_HHMMSS>__<slug>/`. It is not an unlink, for two reasons:
this holds an engagement's paperwork, and the person clicking is not necessarily the
person who could recreate it. Emptying `trash/` is a separate, deliberate act.

The flow is deliberately hard to trigger by accident:

- `GET /clients/{slug}/delete` only renders a confirmation page. It names the client
  and lists every generated file by name, via `deletion_preview()`.
- `POST` requires `confirm=yes`; anything else is a 400. Only the confirmation page
  sends it, and the security gate already refuses cross-site posts.
- On the page, the safe choice ("No, keep this client") is the primary-styled button;
  the destructive one is red and second.

`trash/` is gitignored alongside `clients/` and `output/`.

## Validation

One function, two front ends: `validate_client.validate_client(client, form_keys=None)`.

- **Errors block.** Missing display name, a person with no name/role/DOB/address, a
  residence with no county, or a missing `exemption.category` when a selected form has
  a category checkbox.
- **Warnings inform.** They never block. The useful ones are generated from the
  mappings themselves: `_blank_variable_warnings` reads each selected mapping's `from:`
  keys and reports any that resolve empty — which is what catches a missing
  `parcel_id` on the Appraiser forms or a missing `job_title` on the Clerk form. A new
  mapping is covered the moment it lands, with no code change.

Same checks from the terminal:

```bash
python validate_client.py --client clients/lastname_firstname.yaml
python validate_client.py --all                       # every client file
python validate_client.py --client ... --forms hillsborough_clerk_redaction
python validate_client.py --all --strict              # warnings fail too
```

Exits non-zero when there are errors.

## Layout

```
webapp/
  desktop.py         the app window (pywebview); server on a background thread
  main.py            app, middleware, socket binding, the golden rules in one docstring
  security.py        Host / cross-site / session-token gate
  deps.py            slug/filename guards, templates, url_for encoding, report collector
  formparse.py       bracket form keys -> nested dict
  model.py           nested dict -> client YAML shape
  routers/
    clients.py       list, new/edit, HTMX fragments
    generate.py      generate, preview, download, worklist
    forms.py         /forms overview
  templates/         base.html + one per screen, partials under clients/
  static/
    app.css          the only stylesheet
    app.js           ~30 lines: next row index, remove row
    vendor/htmx.min.js
```
