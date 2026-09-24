# Running it on a Mac

Two ways. The first needs nothing installed and is what you want.

## 1. The ready-built app (nothing to install)

A macOS runner builds `Privacy Toolkit.app` from this repo and attaches it to a
release. Nothing on the Mac: no Python, no Terminal, no launcher.

**Download:** <https://github.com/JohnVadis/privacy-toolkit/releases/tag/macos-build>

There are two files, one per chip. A Mac build runs only on the architecture it was
made for, so the wrong one simply won't open:

| Their Mac | File |
|---|---|
| Apple Silicon (M1–M4) — anything sold since late 2020 | `...for apple-silicon Macs.zip` |
| Intel | `...for intel Macs.zip` |

( menu → About This Mac says which.)

Unzip it, and move the whole `Privacy Toolkit` folder somewhere they keep work.
Inside is `Privacy Toolkit.app` plus the files it reads and writes — `clients/`,
`output/`, `forms/`, `sites.yaml`. Double-click the app.

**Keep the folder together.** The app writes client files *beside* itself, so moving
only the .app leaves the cases behind.

The first launch, macOS will refuse: *"Apple could not verify… is free of malware"*.
The app is ad-hoc signed — enough that Apple Silicon will run it at all — but not
signed with a paid Developer ID, which is what silences that dialog.
**Right-click the app → Open → Open.** Once only.

A tester who instead sees *"the application is damaged"* has a quarantine flag on the
download, not a bad build: `xattr -cr` the folder, then open it as above. Both of
these are spelled out in the release notes and in READ ME FIRST.txt inside the
folder, so they shouldn't need to ask.

### Rebuilding it

After any change to the toolkit, push to `master`, then Actions →
**build macOS app** → *Run workflow*. It runs the full test suite first and refuses
to publish an app whose engine is broken. The release is replaced in place, so the
download link never changes.

Both architectures build in parallel. Apple Silicon takes about 90 seconds; the Intel
runners are scarcer and can sit queued for a while, so the Apple Silicon file often
appears well before the Intel one. That's a queue, not a failure.

```bash
gh workflow run build-macos.yml --ref master     # from a terminal, if you prefer
```

## 2. From source, with the launcher

Needs Python on the Mac. Use this if you're changing the code and want to see
changes without waiting for a build.

### Getting it there

Copy `Privacy Toolkit (test copy).zip` to the Mac however you like — USB, AirDrop,
email, a shared folder. Unzip it by double-clicking. You'll get a folder called
`Privacy Toolkit (test copy)`; put it wherever you'd keep a work folder (Documents is
fine, the Desktop is fine).

Don't run it from inside the Downloads folder long-term — client files get written
next to the app, and Downloads is the folder people empty without looking.

### First run

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

### If it says Python isn't installed

macOS doesn't ship a Python you can rely on. Install one:

- **Easiest:** download the macOS installer from <https://www.python.org/downloads/>
  and run it. Version 3.12 is the safe choice — every library the toolkit pins has a
  ready-built package for it.
- **If you use Homebrew:** `brew install python@3.12`

Then double-click the launcher again.

The launcher looks for `python3.12`, then `python3.11`, then plain `python3`, and
uses the first it finds.

### The two permissions macOS will ask for

**Screen Recording** — only for the **Comment** button, which takes a picture of the
app window so you can point at what's wrong. macOS asks the first time you use it:
System Settings → Privacy & Security → Screen Recording → allow the Terminal (or
`Privacy Toolkit`, if you're running a packaged build).

**Then quit the app and start it again.** macOS does not extend a newly granted
Screen Recording permission to a process that is already running, so Comment keeps
refusing until the app is restarted. This is the step everyone misses.

Until it is granted the capture is refused outright — `ScreenPermissionNeeded`, which
the route turns into a 503 naming the setting. That refusal is deliberate and worth
keeping: without permission `screencapture` still returns an image of the right
*size* showing the desktop, so the feature looks like it worked and quietly attaches
a photo of whatever else the worker had open to an email. A beta tester hit exactly
that.

The window itself is found by **owner PID**, not by title. macOS reveals window
titles only to a process that already holds this permission, so a title lookup fails
on precisely the machines where it matters, and fails silently by grabbing the whole
screen. The webview runs in our own process, so the PID always matches.

Everything else — filling forms, importing documents, building worklists, exporting —
needs no permission at all.

**Files and Folders** — if you keep the toolkit in Documents or Desktop, macOS may
ask the app for access the first time it writes there. Allow it, or move the folder
somewhere outside those two.

### Where your client data lives

Exactly where it does on Windows: `clients/` and `output/` **inside the toolkit
folder**, next to the launcher. Nothing is written to `~/Library`, nothing syncs, and
nothing leaves the Mac.

If you later build the packaged `.app` (`python build_exe.py` on the Mac), the data
sits in the folder *containing* the `.app`, never inside the bundle — so replacing the
app with a newer one never touches the cases.

### What to check on the first run

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

### When something breaks

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
