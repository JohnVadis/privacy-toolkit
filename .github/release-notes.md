## Download the one that matches your Mac

| Your Mac | File |
|---|---|
| **Apple Silicon** — M1, M2, M3, M4 | `Privacy Toolkit for apple-silicon Macs.zip` |
| **Intel** | `Privacy Toolkit for intel Macs.zip` |

Not sure which you have?  menu → **About This Mac**. If it says *Chip: Apple M…*
take the Apple Silicon one; if it says *Processor: Intel…* take the Intel one.

## Then

1. **Unzip it.** You get a folder called `Privacy Toolkit`.
2. **Move the whole folder** somewhere you keep work — Documents, Desktop, a drive.
   Keep it together: the app saves your client files into the folders beside it.
3. **Right-click `Privacy Toolkit.app` → Open → Open.**

That last step is needed only the first time. macOS blocks any app that hasn't been
bought an Apple developer certificate, and says so in a way that sounds alarming —
double-clicking normally will just refuse. Right-click → Open gets past it once, and
after that it opens like anything else.

There is nothing to install. No Python, no Terminal, no setup.

## What's in the folder

- **Privacy Toolkit.app** — the thing you open
- **READ ME FIRST.txt** — the same instructions, plus what the app does and doesn't do
- **clients/** — your client files live here (one made-up sample to start with)
- **output/** — filled forms land here
- **forms/**, **sites.yaml** — the county forms and the opt-out list it works from

## If it says the app is "damaged"

It isn't. That is macOS reacting to a quarantine flag on a downloaded file. Open
Terminal, type `xattr -cr ` (with the trailing space), drag the `Privacy Toolkit`
folder onto the window, and press Return. Then open the app as above.

---

Built automatically from `master` on real Macs. The full test suite runs first, and
a build is only published if it passes.
