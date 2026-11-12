import pathlib

import pytest

from bridge_app.main import APP_FOOTER_PLACEHOLDER, _get_shared_footer, _inject_shared_footer

STATIC = pathlib.Path("bridge_app/static")
DASHBOARD_PAGES = ["spending.html", "net_worth.html", "fire.html"]


def test_canonical_footer_is_extracted_from_index():
    footer = _get_shared_footer()
    assert footer.startswith('<footer class="app-footer">')
    assert footer.endswith("</footer>")
    assert "footer-brand" in footer
    assert "v2.11 · " in footer
    assert footer in (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("page", DASHBOARD_PAGES)
def test_dashboard_pages_use_placeholder_not_duplicate_footer(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert html.count(APP_FOOTER_PLACEHOLDER) == 1
    assert '<footer class="app-footer">' not in html


@pytest.mark.parametrize("page", DASHBOARD_PAGES)
def test_injected_page_contains_index_footer(page):
    html = _inject_shared_footer((STATIC / page).read_text(encoding="utf-8"))
    assert APP_FOOTER_PLACEHOLDER not in html
    assert _get_shared_footer() in html
