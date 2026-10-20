"""Adding and removing opt-out sites, from the engine and from the app."""
from __future__ import annotations

import shutil

import pytest
import yaml

import build_worklist as BW
import paths
import sites_edit
from client_context import EXAMPLE_CLIENT, build_context


@pytest.fixture(autouse=True)
def restore_sites():
    """Every test here edits the shared list; put it back afterwards."""
    original = paths.SITES_FILE.read_bytes()
    yield
    paths.SITES_FILE.write_bytes(original)
    shutil.rmtree(paths.ROOT / sites_edit.BACKUP_DIRNAME, ignore_errors=True)


def add(**kw):
    args = dict(name="Acme People Search", url="https://acme.example.com/optout",
                category="data_broker", needs=["full_name", "email"], notes="Confirm by email.")
    args.update(kw)
    return sites_edit.add_site(args.pop("name"), args.pop("url"), args.pop("category"), **args)


def keys():
    return [s["key"] for s in sites_edit.load()]


class TestEngine:
    def test_saving_unchanged_keeps_every_setting(self):
        before = sites_edit.load()
        sites_edit.save(before)
        assert sites_edit.load() == before

    def test_saving_keeps_the_explanatory_header(self):
        header = sites_edit._header(paths.SITES_FILE.read_text(encoding="utf-8"))
        sites_edit.save(sites_edit.load())
        text = paths.SITES_FILE.read_text(encoding="utf-8")
        assert text.startswith(header)
        assert "# --- Data brokers" in text and "# --- Search engines (last)" in text

    def test_an_added_site_goes_at_the_end_of_its_section(self):
        site = add()
        assert site["key"] == "acme_people_search"
        sites = sites_edit.load()
        i = keys().index("acme_people_search")
        assert sites[i - 1]["category"] == "data_broker"
        assert sites[i + 1]["category"] != "data_broker"
        assert sites[-1]["category"] == "search_engine"      # Google stays last

    def test_an_added_site_passes_the_catalog_rules(self):
        add()
        known = set(build_context(EXAMPLE_CLIENT))
        for s in BW.load_sites():
            assert s["opt_out_url"].startswith("https://")
            assert set(s.get("needs", [])) <= known
        assert len(keys()) == len(set(keys()))

    def test_a_bare_web_address_gets_https(self):
        assert add(url="acme.example.com/optout")["opt_out_url"] == "https://acme.example.com/optout"

    @pytest.mark.parametrize("kw, message", [
        ({"name": ""}, "name"),
        ({"url": "http://acme.example.com"}, "https://"),
        ({"url": "not a link"}, "https://"),
        ({"category": "nonsense"}, "kind"),
        ({"needs": ["ssn"]}, "Unknown"),
        ({"name": "Spokeo"}, "already"),
    ])
    def test_bad_input_is_refused_with_a_reason(self, kw, message):
        before = paths.SITES_FILE.read_bytes()
        with pytest.raises(sites_edit.SiteError, match=message):
            add(**kw)
        assert paths.SITES_FILE.read_bytes() == before

    def test_a_link_already_listed_is_refused(self):
        existing = sites_edit.load()[0]["opt_out_url"]
        with pytest.raises(sites_edit.SiteError, match="already"):
            add(url=existing)

    def test_removing_leaves_everything_else_untouched(self):
        before = sites_edit.load()
        sites_edit.remove_site(before[1]["key"])
        assert sites_edit.load() == before[:1] + before[2:]

    def test_every_change_keeps_a_backup_and_can_be_undone(self):
        before = sites_edit.load()
        removed = sites_edit.remove_site(before[0]["key"])
        assert removed["_backup"] == sites_edit.latest_backup().name
        after_remove = sites_edit.load()
        sites_edit.undo(removed["_backup"])
        assert sites_edit.load() == before
        # the undo kept what it replaced, so it can itself be recovered
        kept = yaml.safe_load(sites_edit.latest_backup().read_text(encoding="utf-8"))
        assert kept == after_remove

    def test_undo_refuses_once_something_else_changed(self):
        sites = sites_edit.load()
        removed = sites_edit.remove_site(sites[0]["key"])
        sites_edit.move_site(sites[2]["key"], "up")
        moved = sites_edit.load()
        with pytest.raises(sites_edit.SiteError, match="changed since"):
            sites_edit.undo(removed["_backup"])
        assert sites_edit.load() == moved

    def test_moving_stays_inside_the_section(self):
        sites = sites_edit.load()
        first, second = sites[0]["key"], sites[1]["key"]
        sites_edit.move_site(second, "up")
        assert keys()[:2] == [second, first]
        last_broker = [s["key"] for s in sites_edit.load() if s["category"] == "data_broker"][-1]
        order = keys()
        sites_edit.move_site(last_broker, "down")            # next one is real estate
        assert keys() == order

    def test_a_removed_site_keeps_its_history_in_the_worklist(self, saved_client):
        key = sites_edit.load()[0]["key"]
        BW.build_worklist(saved_client)
        BW.record_progress(saved_client, key, status=BW.STATUSES[-1])
        sites_edit.remove_site(key)
        stats = {}
        BW.build_worklist(saved_client, stats=stats)
        assert stats["carried_over"] == 1


class TestPage:
    def test_the_page_lists_every_site(self, app_client):
        page = app_client.get("/optout-sites")
        assert page.status_code == 200
        for s in sites_edit.load():
            assert s["name"].replace("'", "&#39;") in page.text

    def test_adding_from_the_page(self, app_client):
        r = app_client.post("/optout-sites/add", follow_redirects=False, data={
            "name": "Acme People Search", "opt_out_url": "https://acme.example.com/optout",
            "category": "data_broker", "needs[]": ["full_name", "email"],
            "notes": "", "client_needed": "", "supports_agent": "1"})
        assert r.status_code == 303
        site = next(s for s in sites_edit.load() if s["key"] == "acme_people_search")
        assert site["needs"] == ["full_name", "email"] and site["supports_agent"] is True
        assert "Acme People Search was added" in app_client.get(r.headers["location"]).text

    def test_a_refused_add_keeps_what_was_typed(self, app_client):
        r = app_client.post("/optout-sites/add", data={
            "name": "Acme People Search", "opt_out_url": "ftp://nope",
            "category": "data_broker"})
        assert r.status_code == 400
        assert "https://" in r.text and "Acme People Search" in r.text

    def test_removing_and_undoing_from_the_page(self, app_client):
        before = sites_edit.load()
        key = before[0]["key"]
        r = app_client.post(f"/optout-sites/{key}/remove", follow_redirects=False)
        assert r.status_code == 303 and key not in keys()
        page = app_client.get(r.headers["location"]).text
        assert "Undo" in page
        backup = sites_edit.latest_backup().name
        assert f'value="{backup}"' in page
        app_client.post("/optout-sites/undo", data={"backup": backup})
        assert sites_edit.load() == before

    def test_a_site_with_a_broken_key_does_not_break_the_page(self, app_client):
        sites = sites_edit.load()
        sites[0] = dict(sites[0], key="")
        paths.SITES_FILE.write_text(yaml.safe_dump(sites), encoding="utf-8")
        page = app_client.get("/optout-sites")
        assert page.status_code == 200 and "fix in sites.yaml" in page.text

    def test_the_worklist_page_links_here(self, app_client, saved_client):
        page = app_client.get("/clients/Example Client/worklist")
        assert "/optout-sites" in page.text

    def test_another_site_cannot_remove_one(self, raw_client):
        key = sites_edit.load()[0]["key"]
        raw_client.post(f"/optout-sites/{key}/remove",
                        headers={"Sec-Fetch-Site": "cross-site"})
        assert key in keys()
