from inspect import Parameter, signature

import adk_agent
import claude_agent_sdk_agent
import langgraph_agent
from run import FRAMEWORKS, build_parser


def test_framework_choices_map_to_adapters() -> None:
    assert FRAMEWORKS == {
        "langgraph": langgraph_agent.run_episode,
        "adk": adk_agent.run_episode,
        "claude-agent-sdk": claude_agent_sdk_agent.run_episode,
    }
    parser = build_parser()
    for framework in FRAMEWORKS:
        args = parser.parse_args(["run", "--framework", framework, "--output", "runs/test"])
        assert args.framework == framework


def test_adapters_share_the_required_episode_signature() -> None:
    for adapter in FRAMEWORKS.values():
        parameters = list(signature(adapter).parameters.values())
        assert [parameter.name for parameter in parameters] == [
            "case",
            "model_name",
            "effort",
            "skills",
            "recorder",
            "teaching",
            "test_model",
        ]
        assert all(parameter.default is Parameter.empty for parameter in parameters[:6])
        assert parameters[6].kind is Parameter.KEYWORD_ONLY
        assert parameters[6].default is None
