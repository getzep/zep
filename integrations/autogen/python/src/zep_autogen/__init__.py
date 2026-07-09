from .exceptions import ZepDependencyError

try:
    # Check for required AutoGen dependencies - just test import
    import autogen_core.memory  # noqa: F401
    import autogen_core.model_context  # noqa: F401

    from .graph_memory import ZepGraphMemory

    # Import our integration
    from .memory import DEFAULT_CONTEXT_TEMPLATE, ContextBuilder, ContextInput, ZepUserMemory
    from .provisioning import UserSetupHook, ensure_thread, ensure_user
    from .tools import create_add_graph_data_tool, create_search_graph_tool

    __all__ = [
        "ZepUserMemory",
        "ZepGraphMemory",
        "create_search_graph_tool",
        "create_add_graph_data_tool",
        "ZepDependencyError",
        "ensure_user",
        "ensure_thread",
        "UserSetupHook",
        "ContextBuilder",
        "ContextInput",
        "DEFAULT_CONTEXT_TEMPLATE",
    ]

except ImportError as e:
    raise ZepDependencyError(framework="AutoGen", install_command="pip install zep-autogen") from e
