"""Website test harness against local fixtures."""

from app.sitecheck import run_site_check


async def test_good_site_passes(session, site_server):
    report = await run_site_check(session, f"{site_server}/good.html")
    failed = [c for c in report["checks"] if not c["passed"]]
    assert report["passed"] is True, failed
    assert report["title"] == "Good Site"
    assert report["summary"]["failed"] == 0


async def test_bad_site_flags_specific_problems(session, site_server):
    report = await run_site_check(session, f"{site_server}/bad.html")
    assert report["passed"] is False
    failed = {c["name"] for c in report["checks"] if not c["passed"]}
    assert "images_have_alt" in failed
    assert "inputs_have_labels" in failed
    assert "links_not_broken" in failed


async def test_required_selector_and_text(session, site_server):
    good = await run_site_check(
        session,
        f"{site_server}/good.html",
        required_selectors=["#go"],
        required_text=["Product Catalog"],
    )
    assert good["passed"] is True

    missing = await run_site_check(session, f"{site_server}/good.html", required_text=["Nonexistent phrase"])
    failed = {c["name"] for c in missing["checks"] if not c["passed"]}
    assert "text:Nonexistent phrase" in failed
