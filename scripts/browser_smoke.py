"""Drive the shipped HTML console. Run against a disposable workspace with a running worker."""
import argparse
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--channel", choices=["chrome", "msedge"], help="Use an installed browser")
    args = parser.parse_args()
    token = os.getenv("AGENTGUARD_API_TOKEN") or Path(os.getenv("AGENTGUARD_TOKEN_FILE", ".agentguard/token")).read_text().strip()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel)
        page = browser.new_page(viewport={"width":1320, "height":920})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(os.getenv("AGENTGUARD_URL", "http://127.0.0.1:8000"))
        assert page.evaluate("""async () => {
            const image = new Image();
            image.src = '/prism.webp';
            await image.decode();
            return image.naturalWidth > 0;
        }""")
        page.get_by_label("Workspace token").fill(token)
        with page.expect_response(lambda r: r.url.endswith("/v1/workspace")) as workspace:
            page.get_by_role("button", name="Connect", exact=True).click()
        capability = workspace.value.json()
        new_run = page.get_by_role("button", name="New research run")
        new_run.focus()
        page.keyboard.press("Enter")
        expect(page.get_by_label("Research question")).to_be_focused()
        page.get_by_role("button", name="Close", exact=True).click()
        expect(new_run).to_be_focused()
        web = page.locator('#source option[value="web"]')
        expect(web).to_have_count(1)
        if capability["web_enabled"]:
            expect(web).to_be_enabled()
            expect(web).to_have_text("Live web")
        else:
            expect(web).to_be_disabled()
            expect(web).to_contain_text("Live web (")
        page.get_by_role("button", name="Evidence packs", exact=True).click()
        page.get_by_label("Pack name").fill("Museum field notes")
        page.get_by_label("Title", exact=True).fill("Collection inventory")
        page.get_by_label("Text", exact=True).fill("The museum holds seven maps.")
        page.get_by_label("Source URL (optional)").fill("https://example.org/collection")
        page.get_by_text("Acceptance checks (optional)", exact=True).click()
        page.get_by_role("button", name="Add check", exact=True).click()
        page.get_by_label("Test question").fill("How many maps does the museum hold?")
        page.get_by_label("Expected text (optional)").fill("seven maps")
        page.get_by_label("Document number (optional)").fill("1")
        page.get_by_role("button", name="Save pack").click()
        page.get_by_role("button", name="Museum field notes").first.click()
        page.get_by_role("button", name="Research this pack").click()
        page.get_by_label("Research question").fill("How many maps does the museum hold?")
        page.get_by_role("button", name="Queue research").click()
        expect(page.locator("#detail")).to_contain_text("The museum holds seven maps. [S1]", timeout=30000)
        expect(page.get_by_role("link", name="Collection inventory · document 1")).to_have_attribute("href", "https://example.org/collection")
        page.get_by_role("button", name="Evaluations", exact=True).click()
        page.locator("#eval-pack").select_option(label="Museum field notes")
        page.get_by_role("button", name="Queue evaluation").click()
        expect(page.locator("#detail")).to_contain_text("29/31 scenarios passed", timeout=60000)
        page.get_by_text("Failed trials (2)", exact=True).click()
        expect(page.locator(".failures li")).to_have_count(2)
        expect(page.locator(".failures li").first).to_be_visible()
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=args.screenshot)
        page.get_by_role("button", name="Assess release", exact=True).click()
        page.locator("#gate-profile").select_option("guarded-extractive")
        page.locator("#gate-form button").click()
        expect(page.locator("#decision")).to_contain_text("Decision · pass")
        expect(page.locator("#decision")).to_contain_text("Pack acceptance checks")
        page.set_viewport_size({"width":390,"height":844})
        for name in ["Release gates", "Evidence packs", "Evaluations", "Runs"]:
            page.get_by_role("button", name=name, exact=True).click()
            expect(page.locator("#breadcrumb")).to_have_text(name)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), name
        new_run.click()
        expect(page.get_by_label("Evidence source")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="Close", exact=True).click()
        page.locator(".history-row").first.click()
        expect(page.locator("#detail")).to_be_focused()
        page.get_by_role("button", name="Disconnect", exact=True).click()
        expect(page.get_by_role("heading", name="Open workspace")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        browser.close()
    print("Browser workflow passed: connect, publish, cite, evaluate, gate, mobile layout, disconnect.")


if __name__ == "__main__":
    main()
