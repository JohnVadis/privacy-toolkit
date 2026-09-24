# Running it on a Mac

The toolkit runs the same on macOS as on Windows — same engine, same forms, same
files. Only the launcher and a couple of OS permissions differ.

**Nothing here has been run on a Mac yet.** The platform-specific code is covered by
`tests/test_platforms.py` with the platform patched, but a first real run is a first
real run. The last section says exactly what to check and what to do when something
doesn't work.

## Getting it there

Copy `Privacy Toolkit (test copy).zip` to the Mac however you like — USB, AirDrop,
email, a shared folder. Unzip it by double-clicking. You'll get a folder called
`Privacy Toolkit (test copy)`; put it wherever you'd keep a work folder (Documents is
fine, the Desktop is fine).

Don't run it from inside the Downloads folder long-term — client files get written
next to the app, and Downloads is the folder people empty without looking.

## First run

Double-click **`Start Privacy Toolkit.command`**.

macOS will probably refuse the first time: *"Start Privacy Toolkit.command cannot be
opened because it is from an unidentified developer."* That's Gatekeeper, and it says
this about anything not bought an Apple developer certificate.

**Right-click the file → Open → Open.** You only do this once; after that
double-clicking works normally.

If that doesn't clear it, open Terminal in the folder and run:

```bash
xattr -dr com.apple.quarantine "Privacy Toolkit (test copy)"
chmod +x "Start Privacy Toolkit.command"
```

The `chmod` shouldn't be needed — the zip carries the execute bit deliberately — but
some transfer routes (especially re-zipping on Windows, or certain email gateways)
strip it.

A Terminal window opens and the launcher:

1. finds Python and builds a private environment in `.venv/` (about a minute, needs
   internet, once only)
2. installs what the app needs
3. opens the app window

**Leave the Terminal window open while you work.** Closing it closes the app. That's
the same as the black window on Windows.

## If it says Python isn't installed

macOS doesn't ship a Python you can rely on. Install one:

- **Easiest:** download the macOS installer from <https://www.python.org/downloads/>
  and run it. Version 3.12 is the safe choice — every library the toolkit pins has a
  ready-built package for it.
- **If you use Homebrew:** `brew install python@3.12`

Then double-click the launcher again.

The launcher looks for `python3.12`, then `python3.11`, then plain `python3`, and
uses the first it finds.

## The two permissions macOS will ask for

**Screen Recording** — only for the **Comment** button, which takes a picture of the
app window so you can point at what's wrong. macOS asks the first time you use it:
System Settings → Privacy & Security → Screen Recording → allow the Terminal (or
`Privacy Toolkit`, if you're running a packaged build). Until it's granted the
capture is refused outright rather than attaching a blank or wrong image to an email.

Everything else — filling forms, importing documents, building worklists, exporting —
needs no permission at all.

**Files and Folders** — if you keep the toolkit in Documents or Desktop, macOS may
ask the app for access the first time it writes there. Allow it, or move the folder
somewhere outside those two.

## Where your client data lives

Exactly where it does on Windows: `clients/` and `output/` **inside the toolkit
folder**, next to the launcher. Nothing is written to `~/Library`, nothing syncs, and
nothing leaves the Mac.

If you later build the packaged `.app` (`python build_exe.py` on the Mac), the data
sits in the folder *containing* the `.app`, never inside the bundle — so replacing the
app with a newer one never touches the cases.

## What to check on the first run

In rough order of "most likely to be wrong":

- [ ] The launcher opens at all (Gatekeeper, execute bit)
- [ ] The environment builds — watch for a package that won't install
- [ ] The app window opens, and isn't blank
- [ ] The sample client's forms generate: **Generate forms** → all four
- [ ] A generated PDF opens and looks right — values on their lines, boxes ticked
- [ ] **Documents** → paste a few rows → the picker lists them
- [ ] **Export case** downloads a zip
- [ ] **Comment** → the picture is *the app window*, not your whole screen

That last one is the macOS-specific code most worth eyeballing.

## When something breaks

The most likely failure by far is **a library that won't install**. The versions in
`requirements.txt` are pinned exactly, on purpose — reportlab decides where text lands
on a filed form, so it isn't a thing to leave floating. But a pin that has a ready-built
package on Windows may not have one for the Python you installed on the Mac.

It looks like a wall of red text mentioning `error: subprocess-exited-with-error`, or
`Building wheel for ... failed`, usually naming `reportlab`, `pypdfium2` or `pyobjc`.

If that happens: install Python 3.12 specifically (see above) and try again, since
that has the widest package coverage. If it still fails, send me the last 30 lines of
the error — the fix is to adjust one pin, and it needs to be the right one.

For anything else, the Terminal window holds the actual error. That text is what
makes it fixable; a screenshot of the app window usually isn't.
