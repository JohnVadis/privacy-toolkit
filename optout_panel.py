#!/usr/bin/env python3
"""
optout_panel.py — the Opt-out Assistant window. A small panel beside the browser.

    python optout_panel.py                                  # pick the client in the window
    python optout_panel.py --client clients/<name>.yaml     # start with one chosen

The same session as `optout_assist.py --terminal`, with buttons instead of letters:
pick a client and the sites, then for each site — find the listing, press "Fill
the form", check it, solve the CAPTCHA and click the site's own Submit button, and
say what happened. Each answer is written to the client's worklist.

It is a tkinter window, not a page in the browser, on purpose: buttons injected into
a broker's page could be pressed by that page's own scripts. Nothing a site runs can
reach this window, and nothing here submits anything (CLAUDE.md rule 3).
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

from build_worklist import WORKLIST_FILENAME, load_sites
from client_context import (ClientDataError, build_context, list_clients,
                            load_client, output_dir, person_roles, variable_label)
from optout_assist import (VERIFY_AFTER_DAYS, Browser, fill_page, off_site,
                           record_site, site_domain, site_status)

# How a worklist status reads to the person using the window.
STATUS_TEXT = {
    "not_started": "not started", "submitted": "submitted",
    "verification_sent": "waiting on email", "verified": "verified",
    "removed": "removed", "rejected": "rejected", "not_removable": "can't be removed",
}

CATEGORY_TEXT = {"data_broker": "Data brokers", "property_site": "Real estate",
                 "maps": "Maps", "search_engine": "Search engines"}

OUTCOME_TEXT = {"submitted": "Submitted", "verification_sent": "Waiting on email",
                "skipped": "Skipped", "unrecorded": "Done, but NOT recorded"}


@dataclass
class Session:
    """Where the worker is in the list. No window code, so it can be tested alone."""
    client: dict
    person: str | None
    sites: list[dict]
    index: int = 0
    outcomes: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self):
        self.ctx = build_context(self.client, self.person)

    @property
    def site(self) -> dict | None:
        return self.sites[self.index] if self.index < len(self.sites) else None

    @property
    def finished(self) -> bool:
        return self.site is None

    def values(self) -> list[tuple[str, str]]:
        """(label, value) for what this site asks for — the copy/paste list."""
        return [(variable_label(v), str(self.ctx.get(v))) for v in
                (self.site or {}).get("needs", []) if self.ctx.get(v)]

    def record(self, status: str, ref: str = "") -> str:
        """Write the outcome to the worklist and move on. Returns the re-check date.

        Raises ClientDataError and stays on this site if it couldn't be written.
        """
        verify = record_site(self.client, self.site, self.ctx, status, ref)
        self._advance(status)
        return verify

    def skip(self, outcome: str = "skipped"):
        self._advance(outcome)

    def _advance(self, outcome: str):
        self.outcomes.append((self.site.get("name", ""), outcome))
        self.index += 1


def _sharp_text():
    """Ask Windows for real pixels, so the window isn't a blurry scaled-up bitmap.

    Without it a high-DPI laptop draws the whole window at 100% and stretches it.
    Elsewhere there's no `windll` and nothing to do.
    """
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


class App:
    PAD = 12

    def __init__(self, client_path=None, person=None, channel="auto"):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        _sharp_text()
        self.root = tk.Tk()
        self.root.title("Opt-out Assistant")
        # Sizes are in 96-dpi pixels, scaled to the screen so they mean the same thing.
        self.scale = self.root.winfo_fpixels("1i") / 96
        px = lambda n: int(n * self.scale)  # noqa: E731
        self.root.geometry(f"{px(440)}x{px(640)}")
        self.root.minsize(px(400), px(560))
        self.root.attributes("-topmost", True)    # stays visible over the browser
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        # Started with pythonw there is no console: an error in a button would
        # vanish and the window would just seem to do nothing. Say so instead.
        self.root.report_callback_exception = self._unexpected

        style = ttk.Style(self.root)
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Site.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Muted.TLabel", foreground="#555555")
        style.configure("Warn.TLabel", foreground="#9a5b00")
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=8)

        self.browser = Browser(channel)
        self.session: Session | None = None
        self.frame = None
        self.preset_client, self.preset_person = client_path, person
        self.show_setup()

    # -- plumbing ----------------------------------------------------------
    def new_screen(self):
        if self.frame is not None:
            self.frame.destroy()
        self.frame = self.ttk.Frame(self.root, padding=self.PAD)
        self.frame.pack(fill="both", expand=True)
        return self.frame

    def label(self, parent, text, style=None, wrap=400, **pack):
        lbl = self.ttk.Label(parent, text=text, wraplength=int(wrap * self.scale),
                             justify="left",
                             **({"style": style} if style else {}))
        lbl.pack(anchor="w", **({"pady": (0, 6)} | pack))
        return lbl

    def busy(self, text):
        self.status.configure(text=text)
        self.root.config(cursor="watch")
        self.root.update_idletasks()

    def idle(self, text=""):
        self.status.configure(text=text)
        self.root.config(cursor="")

    def _unexpected(self, exc_type, exc, tb):
        import traceback
        traceback.print_exception(exc_type, exc, tb)
        try:
            self.idle("")
        except Exception:  # noqa: BLE001 — no status line on this screen
            pass
        self.error("Something went wrong",
                   f"{exc_type.__name__}: {exc}\n\nThe worklist has what was saved so "
                   "far. If the browser was closed, press 'Open the opt-out page again'.")

    def error(self, title, exc):
        from tkinter import messagebox
        messagebox.showerror(title, str(exc), parent=self.root)

    # -- screen 1: who and which sites ---------------------------------------
    def show_setup(self):
        tk, ttk = self.tk, self.ttk
        f = self.new_screen()
        self.label(f, "Opt-out Assistant", "Title.TLabel")
        self.label(f, "Fills in opt-out forms from the client's file. You check each "
                      "one and click Submit yourself — nothing is sent for you.",
                   "Muted.TLabel", pady=(0, 12))

        self.clients = [c for c in list_clients() if not c["error"]]
        if not self.clients:
            self.label(f, "There are no client files yet. Add a client in the Privacy "
                          "Toolkit first, then open this again.", "Warn.TLabel")
            return

        self.label(f, "Client")
        names = [f"{c['display_name'] or c['slug']}"
                 + (f"  ({c['case_number']})" if c["case_number"] else "")
                 for c in self.clients]
        self.client_box = ttk.Combobox(f, values=names, state="readonly")
        self.client_box.pack(fill="x", pady=(0, 10))
        self.client_box.bind("<<ComboboxSelected>>", lambda e: self.on_client())

        self.label(f, "Person")
        self.person_box = ttk.Combobox(f, state="readonly")
        self.person_box.pack(fill="x", pady=(0, 10))
        self.person_box.bind("<<ComboboxSelected>>", lambda e: self.on_person())

        self.label(f, "Sites to work through")
        self.sites_frame = self.scrolling(f)
        self.client_note = self.label(f, "", "Warn.TLabel", pady=(6, 0))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Tick all", command=lambda: self.tick(True)).pack(side="left")
        ttk.Button(row, text="Only not started",
                   command=lambda: self.tick(None)).pack(side="left", padx=6)

        ttk.Button(f, text="Start", style="Big.TButton",
                   command=self.start).pack(fill="x", pady=(14, 0))
        self.status = ttk.Label(f, text="", style="Muted.TLabel", wraplength=int(400 * self.scale))
        self.status.pack(anchor="w", pady=(8, 0))

        start = 0
        if self.preset_client:
            stem = os.path.splitext(os.path.basename(self.preset_client))[0]
            start = next((i for i, c in enumerate(self.clients) if c["slug"] == stem), 0)
        self.client_box.current(start)
        self.on_client()

    def on_client(self):
        try:
            self.client = load_client(self.clients[self.client_box.current()]["path"])
        except ClientDataError as exc:
            # Otherwise Start would run the PREVIOUS client under this one's name.
            self.client, self.roles, self.site_vars = None, [], []
            self.person_box.configure(values=[])
            self.person_box.set("")
            for w in self.sites_frame.winfo_children():
                w.destroy()
            self.client_note.configure(text="")
            return self.error("Couldn't open that client", exc)
        self.roles = person_roles(self.client)
        labels = []
        for role in self.roles:
            name = build_context(self.client, role).get("full_name") or "(no name)"
            labels.append(f"{name}  — {role}")
        self.person_box.configure(values=labels)
        pick = self.roles.index(self.preset_person) if self.preset_person in self.roles else 0
        self.preset_person = None
        self.person_box.current(pick)
        self.on_person()

    def scrolling(self, parent):
        """A frame that scrolls — the catalog is longer than any window."""
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0,
                           background=ttk.Style().lookup("TFrame", "background"))
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        wheel = lambda e: canvas.yview_scroll(int(-e.delta / 120) or  # noqa: E731
                                              (-1 if e.delta > 0 else 1), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def on_person(self):
        tk, ttk = self.tk, self.ttk
        for w in self.sites_frame.winfo_children():
            w.destroy()
        role = self.roles[self.person_box.current()]
        primary = role == self.roles[0]
        # The worklist's Status column is the primary's, so it only says what's
        # been done for them. For anyone else every site starts ticked.
        try:
            self.statuses = site_status(self.client) if primary else {}
        except Exception as exc:  # noqa: BLE001 — a damaged worklist shouldn't block work
            self.statuses = {}
            self.error("Couldn't read the worklist", exc)
        self.site_vars = []
        heading = None
        for site in load_sites():
            category = site.get("category", "")
            if category != heading:
                heading = category
                ttk.Label(self.sites_frame, style="Muted.TLabel",
                          text=CATEGORY_TEXT.get(category, category.replace("_", " ")
                                                 .capitalize())).pack(anchor="w",
                                                                     pady=(6, 0))
            status = self.statuses.get(site.get("key"), "not_started")
            var = tk.BooleanVar(value=status == "not_started")
            text = site.get("name", site.get("key"))
            if site.get("client_needed"):
                text += "   (needs the client)"
            if primary and status != "not_started":
                text += f"   — {STATUS_TEXT.get(status, status)}"
            ttk.Checkbutton(self.sites_frame, text=text, variable=var).pack(anchor="w",
                                                                           pady=1)
            self.site_vars.append((site, var, status))
        needing = [s.get("name") for s, _v, st in self.site_vars
                   if s.get("client_needed") and st == "not_started"]
        self.client_note.configure(text=(
            "Arrange time with the client for: " + ", ".join(needing) + "."
            if needing else ""))

    def tick(self, everything):
        for _site, var, status in self.site_vars:
            var.set(True if everything else status == "not_started")

    def start(self):
        if getattr(self, "client", None) is None:
            return self.idle("Pick a client first.")
        sites = [s for s, var, _ in self.site_vars if var.get()]
        if not sites:
            return self.idle("Tick at least one site.")
        role = self.roles[self.person_box.current()]
        self.session = Session(self.client, role, sites)
        self.busy("Opening the browser…")
        self.show_site()

    # -- screen 2: one site ---------------------------------------------------
    def show_site(self):
        tk, ttk = self.tk, self.ttk
        s = self.session
        if s.finished:
            return self.show_done()
        site = s.site
        f = self.new_screen()

        n, total = s.index + 1, len(s.sites)
        self.label(f, f"Site {n} of {total}  ·  {s.ctx.get('full_name')}", "Muted.TLabel")
        bar = ttk.Progressbar(f, maximum=total, value=s.index)
        bar.pack(fill="x", pady=(0, 10))
        self.label(f, site.get("name", ""), "Site.TLabel")
        if site.get("notes"):
            self.label(f, site["notes"])
        if site.get("client_needed"):
            self.label(f, site["client_needed"], "Warn.TLabel")
        elif not site.get("supports_agent"):
            self.label(f, "This site may not accept a request made on the client's "
                          "behalf — it may need their own email or phone.", "Warn.TLabel")

        self.label(f, "1.  In the browser, find the client's listing (confirm it's them).\n"
                      "2.  Press Fill the form.\n"
                      "3.  Check every value, solve any CAPTCHA, and click the site's "
                      "own Submit button.\n"
                      "4.  Tell me what happened below.", pady=(6, 10))

        ttk.Button(f, text="Fill the form", style="Big.TButton",
                   command=self.fill).pack(fill="x")
        self.result = self.label(f, "", "Muted.TLabel", pady=(6, 6))

        box = ttk.LabelFrame(f, text="Details to copy", padding=8)
        box.pack(fill="x", pady=(4, 10))
        for label, value in s.values():
            row = ttk.Frame(box)
            row.pack(fill="x", pady=1)
            ttk.Button(row, text="Copy", width=6,
                       command=lambda v=value, l=label: self.copy(v, l)).pack(side="right")
            ttk.Label(row, text=f"{label}:  {value}", wraplength=int(310 * self.scale)).pack(side="left")
        ttk.Button(box, text="Open the opt-out page again",
                   command=lambda: self.open_site(site)).pack(anchor="w", pady=(6, 0))

        ref_row = ttk.Frame(f)
        ref_row.pack(fill="x", pady=(0, 8))
        ttk.Label(ref_row, text="Confirmation number (if any):").pack(anchor="w")
        self.ref = ttk.Entry(ref_row)
        self.ref.pack(fill="x")

        ttk.Button(f, text="I clicked Submit", style="Big.TButton",
                   command=lambda: self.done("submitted")).pack(fill="x", pady=(0, 4))
        ttk.Button(f, text="They emailed a verification link",
                   command=lambda: self.done("verification_sent")).pack(fill="x", pady=2)
        bottom = ttk.Frame(f)
        bottom.pack(fill="x", pady=(4, 0))
        ttk.Button(bottom, text="Skip this site", command=self.skip).pack(side="left")
        ttk.Button(bottom, text="Finish early", command=self.show_done).pack(side="right")

        self.status = ttk.Label(f, text="", style="Muted.TLabel", wraplength=int(400 * self.scale))
        self.status.pack(anchor="w", pady=(8, 0))
        self.open_site(site)

    def open_site(self, site):
        self.busy(f"Opening {site.get('name')}…")
        try:
            ok = self.browser.open(site["opt_out_url"])
        except Exception as exc:  # noqa: BLE001 — no browser, or it died starting
            self.idle("")
            return self.error("Couldn't start the browser", exc)
        self.idle("" if ok else "The page didn't finish loading — check the browser, "
                                "or go to the site yourself.")

    def fill(self):
        from tkinter import messagebox
        page = self.browser.current_page()
        if page is None:
            return self.result.configure(text="The browser is closed. Press "
                                              "'Open the opt-out page again'.")
        site = self.session.site
        if off_site(page.url, site) and not messagebox.askyesno(
                "Another website",
                f"The tab in front is on {site_domain(page.url)}, not "
                f"{site_domain(site.get('opt_out_url'))}.\n\nFill the client's details "
                "into it anyway?", default="no", parent=self.root):
            return self.result.configure(text="Not filled. Switch to the site's own tab "
                                              "and press Fill again.")
        self.busy("Filling…")
        try:
            rep = fill_page(page, self.session.ctx, self.session.site)
        except Exception as exc:  # noqa: BLE001 — the tab was closed mid-fill
            self.idle("")
            return self.result.configure(text=f"Couldn't fill this page ({type(exc).__name__}).")
        self.idle("")
        lines = [f"On {site_domain(page.url)}:"]
        if rep["moved_on"]:
            lines.append("The page changed by itself while it was being filled (the "
                         "site's own script). Check what it did before going on.")
        if rep["filled"]:
            lines.append(f"Filled {len(rep['filled'])} field(s): "
                         + ", ".join(variable_label(v) for _, v in rep["filled"]) + ".")
        else:
            lines.append("Nothing to fill on this tab — open the form first, or use "
                         "Copy below.")
        if rep["left"]:
            lines.append("Fill these yourself: " + ", ".join(rep["left"]) + ".")
        if rep["errors"]:
            lines.append(f"{len(rep['errors'])} field(s) couldn't be filled.")
        lines.append("Now check them and click Submit on the site.")
        self.result.configure(text="\n".join(lines))

    def copy(self, value, label):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.idle(f"Copied {label}.")

    def done(self, status):
        from tkinter import messagebox
        ref = self.ref.get().strip()
        while True:
            try:
                verify = self.session.record(status, ref)
                break
            except Exception as exc:  # noqa: BLE001 — any failure to save gets the same choice
                if not messagebox.askretrycancel(
                        "Couldn't save to the worklist",
                        f"{exc}\n\nRetry once it's closed, or Cancel to move on "
                        "(you'll need to note this one by hand).", parent=self.root):
                    self.session.skip("unrecorded")
                    return self.show_site()
        self.show_site()
        self.idle(f"Saved. Re-check that one by {verify}.")

    def skip(self):
        self.session.skip()
        self.show_site()

    # -- screen 3: summary ----------------------------------------------------
    def show_done(self):
        ttk = self.ttk
        s = self.session
        while s and not s.finished:          # "Finish early" — the rest were skipped
            s.skip()
        f = self.new_screen()
        self.label(f, "All done", "Title.TLabel")
        for name, outcome in s.outcomes:
            self.label(f, f"•  {name}: {OUTCOME_TEXT.get(outcome, outcome)}", pady=(0, 2))
        self.label(f, f"\nWatch the email inbox for verification links, and re-check "
                      f"the sites in about {VERIFY_AFTER_DAYS} days. The worklist has "
                      "the dates.", pady=(8, 12))
        path = output_dir(s.client) / WORKLIST_FILENAME
        if path.is_file():
            # The toolkit's one "show this file" helper — platform quirks live there.
            from webapp.deps import reveal
            ttk.Button(f, text="Show the worklist",
                       command=lambda: reveal(path)).pack(fill="x", pady=2)
        ttk.Button(f, text="Start another client",
                   command=self.show_setup).pack(fill="x", pady=2)
        ttk.Button(f, text="Close", command=self.quit).pack(fill="x", pady=2)
        self.status = ttk.Label(f, text="", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(8, 0))

    def quit(self):
        try:
            self.browser.close()
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(client_path=None, person=None, channel="auto"):
    try:
        App(client_path, person, channel).run()
    except Exception as exc:  # noqa: BLE001 — pythonw has no console to print to
        import traceback
        traceback.print_exc()
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"The Opt-out Assistant couldn't start:\n\n{exc}",
                "Opt-out Assistant", 0x10)
        except (AttributeError, OSError):
            pass
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="The Opt-out Assistant window.")
    ap.add_argument("--client", help="client file to start with")
    ap.add_argument("--person", help="person role to start with")
    ap.add_argument("--browser", default="auto", help="msedge | chrome | chromium")
    a = ap.parse_args()
    main(a.client, a.person, None if a.browser == "chromium" else a.browser)
