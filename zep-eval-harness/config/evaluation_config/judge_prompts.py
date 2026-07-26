"""
Judge Prompts

The two LLM-judge rubrics used to score each test case:

- Context completeness (PRIMARY) — did retrieval surface the information the
  golden answer requires? Graded on the retrieved context alone.
- Answer accuracy (SECONDARY) — did the model produce the golden answer from
  that context?

These live in config so every run snapshots the exact rubric that scored it.
Editing a rubric changes the metric, so runs graded by different rubrics are not
comparable — compare the snapshots when a score moves unexpectedly.
"""


def get_accuracy_judge_prompts(
    question: str, golden_answer: str, ai_response: str
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for grading answer accuracy.

    Args:
        question: The original question.
        golden_answer: The expected correct answer.
        ai_response: The AI-generated response to grade.

    Returns:
        Tuple of (system prompt, user prompt).
    """
    system_prompt = """
You are an expert grader that determines if AI responses are correct.
"""

    user_prompt = f"""
I will give you a question, the golden (correct) answer, and an AI-generated response.

Please evaluate if the response is semantically equivalent to the golden answer. Return true ONLY if the response contains ALL the essential information from the golden answer.

<QUESTION>
{question}
</QUESTION>

<GOLDEN ANSWER>
{golden_answer}
</GOLDEN ANSWER>

<AI RESPONSE>
{ai_response}
</AI RESPONSE>

Evaluation Guidelines:
- The response must contain ALL key information from the golden answer (names, locations, actions, etc.)
- The response doesn't need to match exact wording, but must not omit or change critical details
- If the golden answer specifies a specific name, the response must include that name, not a generic term. 
- Some variation is allowed for commonly acceptable names e.g. NYC or New York may be used to refer to New York City
- If the golden answer includes specific details (location, times, etc.), those must be present
- If the response is missing ANY critical information from the golden answer, return false
- If the response adds conversational filler but contains all essential info, return true
- If the response abstains from answering or says it doesn't know, return false

Examples of INCORRECT responses:
- Golden includes a specific person's name → Response uses a generic role/relationship term instead
- Golden includes a specific location → Response omits the location or uses a generic term
- Golden includes a complete message → Response omits part of the message

Examples of CORRECT responses:
- Golden and response have same key information with different wording
- Golden and response have same key information with different, but commonly acceptable names e.g. NYC or New York may be used to refer to New York City
- Response adds conversational elements but preserves all essential details from golden answer

Provide your evaluation.
"""

    return system_prompt, user_prompt


def get_completeness_judge_prompts(
    question: str, golden_answer: str, context: str
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for grading context completeness.

    Args:
        question: The original question.
        golden_answer: The expected answer, defining what information is needed.
        context: Everything retrieval produced — the context block, the union of
            all tool outputs, or both.

    Returns:
        Tuple of (system prompt, user prompt).
    """
    system_prompt = """
You are an expert evaluator assessing whether retrieved context contains adequate information to answer a question.
"""

    user_prompt = f"""
Your task is to evaluate whether the provided CONTEXT contains sufficient information to answer the QUESTION according to what the GOLDEN ANSWER requires.

IMPORTANT: You are NOT evaluating an answer. You are evaluating whether the CONTEXT itself has the necessary information.

<QUESTION>
{question}
</QUESTION>

<GOLDEN ANSWER>
{golden_answer}
</GOLDEN ANSWER>

<CONTEXT>
{context}
</CONTEXT>

Evaluation Guidelines:

1. **COMPLETE**: The context contains ALL information needed to fully answer the question according to the golden answer.
   - All key elements from the golden answer are present
   - Sufficient detail exists to construct a complete answer
   - Historical facts (with past date ranges) ARE valid context

2. **PARTIAL**: The context contains SOME relevant information but is missing key details.
   - Some elements from the golden answer are present
   - Some critical information is missing or incomplete
   - Additional context would be needed for a complete answer

3. **INSUFFICIENT**: The context lacks most or all critical information needed.
   - Key elements from the golden answer are absent
   - Context is off-topic or irrelevant
   - No reasonable answer could be constructed from this context

IMPORTANT section equivalence:
- ALL sections of the context are equally valid sources of information (USER_SUMMARY, FACTS, ENTITIES, EPISODES, DOCUMENT_FACTS, DOCUMENT_ENTITIES, CONTEXT_BLOCK, TOOL_RESULTS, etc.)
- Information found in ANY section counts as present — do not penalize information for appearing in one section versus another
- If a TOOL_RESULTS section is present, it holds the output of every retrieval tool call the agent made. Treat every tool call's output as retrieved context, including outputs the agent may not have used in its answer
- If an element from the golden answer appears anywhere in the context, it is PRESENT
- Do NOT judge how the context was retrieved, how many tool calls were made, or whether some tool calls returned nothing useful — judge only what information the context contains

IMPORTANT temporal interpretation:
- Facts with date ranges (e.g., "2025-10-01 - 2025-10-07") represent WHEN events occurred
- These historical facts remain VALID context even if dated in the past
- Only mark information as missing if it is truly ABSENT from the context
- Do NOT mark facts as "expired" or "outdated" simply because they have past dates
- Date ranges ending before "present" indicate completed/past events, not invalid information

For your evaluation:
- Identify which information elements ARE present in the context (present_elements)
- Identify which information elements are MISSING (truly absent) from the context (missing_elements)
- Historical facts (past date ranges) count as present information
- Provide clear reasoning explaining your completeness assessment

Provide your evaluation.
"""

    return system_prompt, user_prompt
