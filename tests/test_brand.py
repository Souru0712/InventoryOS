"""T4.11: what people read says Vontry, never an earlier product name (C-24, C-55).

Internal identifiers keep the old names on purpose (C-55): the stored origin code
`inventoryos_pos`, and lowercase `stockpulse` in the logger, the CLI, the default database file
and the demo address. Only the product names as people would read them are refused.
"""

import io
import re
import tokenize
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "web" / "templates"

# Any spelling a page or email could show, including the old split logo (Stock<span>Pulse</span>).
OLD_IN_TEMPLATES = re.compile(r"stock\s*(?:<[^>]*>\s*)*pulse|inventory\s*os(?!_pos)", re.I)
# In Python strings, the capitalized product names only: lowercase identifiers stay (C-55).
OLD_IN_STRINGS = re.compile(r"Stock\s*Pulse|STOCKPULSE|Inventory\s*OS|INVENTORYOS")


def test_no_template_or_email_names_an_old_product():
    found = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in sorted(TEMPLATES.rglob("*"))
        if path.suffix in {".html", ".txt"}
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if OLD_IN_TEMPLATES.search(line)
    ]
    assert found == [], "\n".join(found)


def test_no_string_in_the_app_names_an_old_product():
    """Subjects, flash messages, page titles and sender names are Python strings."""
    found = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.STRING and OLD_IN_STRINGS.search(token.string):
                found.append(f"{path.relative_to(ROOT)}:{token.start[0]}: {token.string[:80]}")
    assert found == [], "\n".join(found)


@pytest.mark.parametrize("page", ["/", "/pricing", "/login", "/signup"])
def test_public_pages_say_vontry(db, page):
    with TestClient(app) as client:
        response = client.get(page)
    assert response.status_code == 200, page
    assert "Vontry" in response.text
    assert not OLD_IN_TEMPLATES.search(response.text), page
