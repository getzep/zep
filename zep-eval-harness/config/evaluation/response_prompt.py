"""
Response System Prompt

Defines the system prompt used when generating AI responses during evaluation.
Edit this file to customize the AI's persona and response behavior for your use case.
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
