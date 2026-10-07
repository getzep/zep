"""Prompt hygiene: graph data never enters the system prompt."""

from __future__ import annotations

from agent_with_zep_adk.config import AS_OF_DATE
from agent_with_zep_adk.prompts import ROLE, render_graph_data, render_system_prompt


def _orientation():
    return {
        "graph_id": "test-graph",
        "nodes": [
            {"uuid": "uuid-aster", "name": "Aster 410", "labels": ["Entity", "Product"]},
            {"uuid": "uuid-voss", "name": "Vossbridge Sensors", "labels": ["Entity", "Supplier"]},
        ],
    }


def test_system_prompt_has_no_graph_data():
    prompt = render_system_prompt(
        role=ROLE,
        as_of_date=AS_OF_DATE,
        domain_knowledge="domain knowledge stub",
        orientation=True,
        planning=True,
        include_domain_knowledge=True,
    )
    for name in ["Aster 410", "Vossbridge Sensors", "Northgate", "St. Brigid's Hospital"]:
        assert name not in prompt
    assert "Entity types:" in prompt and "QualityIssue" in prompt


def test_toggles_remove_sections():
    prompt = render_system_prompt(
        role=ROLE,
        as_of_date=AS_OF_DATE,
        domain_knowledge="dk",
        orientation=False,
        planning=False,
        include_domain_knowledge=False,
    )
    assert "Graph orientation" not in prompt
    assert "Domain knowledge" not in prompt
    assert "Planning" not in prompt
    assert "# Role" in prompt and "# Tool rules" in prompt


def test_graph_sample_goes_in_user_turn():
    block = render_graph_data("- n1 Aster 410 [Entity,Product]")
    assert block.startswith('<graph_data source="zep" kind="most_connected_nodes">')
    assert "Aster 410" in block
    assert block.rstrip().endswith("</graph_data>")
