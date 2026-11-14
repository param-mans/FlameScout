from pathlib import Path

import pytest
import yaml

from services.agents.orchestrator import SKILLS_DIR, STRATEGY_PROMPT


@pytest.mark.parametrize("name", ["occupancy-hazards", "size-up-format"])
def test_skill_has_valid_frontmatter(name):
    text = (SKILLS_DIR / name / "SKILL.md").read_text()
    assert text.startswith("---\n")
    meta = yaml.safe_load(text.split("---\n")[1])
    assert meta["name"] == name == Path(SKILLS_DIR / name).name  # the spec: name matches the folder
    assert 40 < len(meta["description"]) <= 1024
    assert text.split("---\n", 2)[2].strip().startswith("# ")


def test_occupancy_table_covers_both_fixture_buildings():
    table = (SKILLS_DIR / "occupancy-hazards" / "SKILL.md").read_text().lower()
    for tenant_type in ("restaurant", "e-bike", "pool or spa supply", "warehouse"):
        assert tenant_type in table


def test_strategy_is_told_to_read_skills_in_its_first_turn_and_never_trust_notes():
    assert "SKILL.md" in STRATEGY_PROMPT and "unverified agent notes" in STRATEGY_PROMPT
