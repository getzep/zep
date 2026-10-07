"""Verify ADK sees the intended tool signatures and schemas."""

from __future__ import annotations

from google.adk.tools.function_tool import FunctionTool

from agent_with_zep_adk import ontology
from agent_with_zep_adk.planner import RetrievalPlan, make_submit_plan
from agent_with_zep_adk.tools import build_tools


def test_retrieval_tool_declarations_hide_closure_dependencies(deps):
    for fn in build_tools(deps):
        declaration = FunctionTool(fn)._get_declaration()
        assert declaration is not None
        properties = declaration.parameters_json_schema["properties"]
        assert "deps" not in properties
        assert "ctx" not in properties


def test_list_nodes_declaration_uses_ontology_labels(deps):
    functions = {fn.__name__: fn for fn in build_tools(deps)}
    declaration = FunctionTool(functions["list_nodes"])._get_declaration()

    label = declaration.parameters_json_schema["properties"]["label"]
    assert label["enum"] == list(ontology.ENTITY_LABELS)


def test_submit_plan_declaration_uses_retrieval_plan_schema(deps):
    declaration = FunctionTool(make_submit_plan(deps))._get_declaration()

    assert declaration is not None
    schema = declaration.parameters_json_schema
    properties = schema["properties"]
    assert "deps" not in properties
    assert "ctx" not in properties
    plan_schema = schema["$defs"]["RetrievalPlan"]
    assert {"subjects", "steps", "evidence_needed", "stop_when"} <= set(plan_schema["properties"])
    assert RetrievalPlan.model_fields.keys() <= plan_schema["properties"].keys()
