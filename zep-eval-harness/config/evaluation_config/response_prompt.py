"""
Response System Prompts

Defines the system prompts used when generating AI responses during evaluation.
Edit this file to customize the AI's persona and response behavior for your use case.

Two prompts, one per retrieval path:
- get_response_system_prompt(): context block only — retrieval already happened.
- get_tool_agent_system_prompt(): tool mode — the model retrieves for itself.
"""


def get_response_system_prompt(context: str) -> str:
    """Return the system prompt for generating AI responses.

    Args:
        context: The formatted context block from Zep graph search results.

    Returns:
        Complete system prompt string with context embedded.
    """
    return f"""
You are an intelligent AI assistant helping a user with their questions.

You have access to the user's conversation history and relevant information in the CONTEXT.

<CONTEXT>
{context}
</CONTEXT>

Using only the information in the CONTEXT, answer the user's questions. Keep responses SHORT - one sentence when possible.
"""


def get_tool_agent_system_prompt(
    context: str | None = None,
    max_iterations: int = 0,
    max_tool_calls: int = 0,
) -> str:
    """Return the system prompt for the tool-calling agent.

    Args:
        context: Pre-retrieved context block, when deterministic retrieval runs
            alongside tools. None when the agent must retrieve everything itself.
        max_iterations: Rounds of tool calling available to the agent.
        max_tool_calls: Total tool calls available to the agent.

    Returns:
        Complete system prompt string.
    """
    context_section = (
        f"""
Some context has already been retrieved for you. It may or may not be enough.

<CONTEXT>
{context}
</CONTEXT>

Use your tools to retrieve anything else the question requires.
"""
        if context
        else """
You have NO information about the user yet. You must use your tools to retrieve
what you need before answering — never answer from assumption or general knowledge.
"""
    )

    budget_section = ""
    if max_tool_calls:
        budget_section = f"""
Retrieval budget: up to {max_tool_calls} tool calls across at most
{max_iterations} rounds. Calling several tools in parallel in one round costs
one round, so batch independent lookups together rather than going one at a time.
"""

    return f"""
You are an intelligent AI assistant helping a user with their questions.

You have tools that retrieve information about the user from long-term memory.
{context_section}{budget_section}
Guidelines:
- Retrieve before you answer. If a question has multiple parts, issue one query per part, in parallel.
- If a search comes back empty or off-target, rephrase the query and try again rather than giving up.
- Once you have what you need, answer directly from the retrieved information.
- Answer only from retrieved information. If it isn't there, say you don't know.
- Keep responses SHORT - one sentence when possible.
"""
