#!/usr/bin/env python3
"""
Common constants, models, and utilities for LongMemEval
"""

import logging
import os
import pandas as pd
from pydantic import BaseModel, Field

# Configuration Constants
RESPONSE_MODEL = "gpt-4o"
GRADER_MODEL = "gpt-4o"
SUMMARY_MODEL = "gpt-4.1"
DATA_PATH = "data"


# Summarization Prompt Templates
LIST_SUMMARIZE_THREAD_CONTEXT_PROMPT_SYSTEM = """Your task is to analyze the provided CONTEXT containing facts with timestamps, summaries, and messages, then generate a summarized list of key facts in bullet format.

Instructions:
- Create bullet points that capture the most important facts from the context
- Pay special attention to:
  * HOW things changed over time (state transitions)
  * WHAT caused changes to happen (causality)
  * HOW MANY of something exists or occurred (counts, quantities)
  * WHEN events happened (specific dates, times, sequences)
  * WHAT the current state is vs previous states
- Use timestamps to understand the chronological order of events
- Track how facts evolved: if something was true at one time but changed later, show both states
- Include specific numbers, dates, names, and quantities when present
- Note relationships between events (what led to what)
- Prioritize facts that show progression, change, or current status
- Write each bullet as a complete, standalone fact
- Keep bullets concise but specific (aim for 10-20 words per bullet)
- Order bullets chronologically when possible
- Use third person (refer to people by name, not "you")
- Use the same language as the input context
- Return ONLY the bulleted facts, no headers or labels

Format each fact as: • [Clear, specific fact with temporal/causal context]
"""

# Question-Answering Summarization Prompt Template
QA_SUMMARIZE_THREAD_CONTEXT_PROMPT_SYSTEM = """Your task is to extract and summarize information from the provided CONTEXT that directly helps answer the QUESTION.

Core Instructions:
- Extract ONLY facts, details, and context that relate to answering the QUESTION
- Synthesize information to show the complete story leading to the current situation
- Focus on what the user needs to know to understand the answer, not just the final result

Temporal Requirements:
- Track how things changed over time in chronological order
- Distinguish between past, current, and future information
- Note when timeframes have expired or are approaching deadlines
- Show the progression of decisions, recommendations, or events
- Prioritize current/relevant facts over outdated information

Content to Include:
- Specific dates, numbers, quantities, and measurements
- Names, preferences, requirements, and constraints  
- Multiple options or alternatives that were considered
- The reasoning behind decisions or recommendations
- Cause-and-effect relationships between events
- Current status vs previous states

Writing Requirements:
- Write as ONE clear, flowing paragraph (not bullets or lists)
- Use third person - refer to the user by name, never as "you"
- Target 60-90 words while including essential details
- Match the language used in the conversation
- Be factual and objective, avoid interpretation

Output Format:
- Return ONLY the summary content
- No labels like "Summary:" or "Context:"
- No prefixes, headers, or formatting
- Start directly with the summarized information
"""

LIST_SUMMARIZE_THREAD_CONTEXT_PROMPT_USER = """
<Context>
# Facts: 
{facts}

# Entities: 
{entities}

# Messages: 
{messages}
</Context>
"""

QA_SUMMARIZE_THREAD_CONTEXT_PROMPT_USER = """
<QUESTION>
{question}
</QUESTION>

<Context>
# Facts: 
{facts}

# Entities: 
{entities}

# Messages: 
{messages}
</Context>
"""

CONTEXT_TEMPLATE_SUMMARY = """
The following context has been intelligently summarized from the user's conversation history to provide the most relevant information for answering the current question.

<SUMMARIZED_CONTEXT>
{summary}
</SUMMARIZED_CONTEXT>

Processing Guidelines for Summarized Context:
- This context has been AI-processed to extract and synthesize the most relevant information
- Temporal information (dates, sequences, changes over time) has been preserved in the summary
- The summary focuses on facts and relationships directly relevant to the current question
- All timestamps and dates mentioned represent when actual events occurred, not when they were discussed
- Information is presented chronologically when temporal order is important
- Use this summarized context as authoritative - it represents the most pertinent information available

Important: The summary has already filtered and organized the information for relevance. Trust the temporal relationships and factual details provided, as they have been extracted from the complete conversation history.
"""


# Context template for search results
CONTEXT_TEMPLATE = """
The following sections contain relevant information for the current conversation. Each section serves a specific purpose in providing context.

<FACTS>
# Timestamped Facts
These facts include datetime stamps that indicate WHEN the actual event occurred, not when it was mentioned or recorded.

Important: If a fact states "something happened last week," the timestamp shows the actual date from last week when the event occurred, not the date when someone mentioned it.

{facts}
</FACTS>

<ENTITIES>
# Key People, Places, and Things  
These are important entities referenced in the conversation, with brief summaries of their relevance.
Format: ENTITY_NAME: description of entity and its role/significance

{entities}
</ENTITIES>

<MESSAGES>
# Relevant Historical Messages
These are the most relevant messages from across the user's entire engagement history, including past conversations and interactions that provide important context for the current discussion.

{messages}
</MESSAGES>

Processing Guidelines:
- FACTS provide historical context with precise timing
- ENTITIES help identify key subjects and their relationships  
- MESSAGES show relevant context from the user's complete interaction history
- Cross-reference information between sections to build complete understanding
"""

# Grading prompts by question type
GRADING_PROMPTS = {
    "temporal-reasoning": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct.

<QUESTION>
B: {question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
A: {response}
</RESPONSE>
""",
    "knowledge-update": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.

<QUESTION>
B: {question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
A: {response}
</RESPONSE>
""",
    "single-session-preference": """
I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.

<QUESTION>
B: {question}
</QUESTION>
<RUBRIC>
{gold_answer}
</RUBRIC>
<RESPONSE>
A: {response}
</RESPONSE>
""",
    "default": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no.

<QUESTION>
B: {question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
A: {response}
</RESPONSE>
""",
}


class Grade(BaseModel):
    is_correct: str = Field(description="yes or no")


def get_summarization_prompts(strategy, question=None):
    """Get the appropriate summarization prompts based on strategy

    Args:
        strategy: "list", "qa", or null/None for no summarization
        question: Required for "qa" strategy, ignored for others

    Returns:
        Tuple of (system_prompt, user_prompt_template) or (None, None) if no summarization
    """
    if strategy == "list":
        return (
            LIST_SUMMARIZE_THREAD_CONTEXT_PROMPT_SYSTEM,
            LIST_SUMMARIZE_THREAD_CONTEXT_PROMPT_USER,
        )
    elif strategy == "qa":
        if question is None:
            raise ValueError("Question is required for QA summarization strategy")
        return (
            QA_SUMMARIZE_THREAD_CONTEXT_PROMPT_SYSTEM,
            QA_SUMMARIZE_THREAD_CONTEXT_PROMPT_USER,
        )
    else:
        # null, None, or any other value means no summarization
        return (None, None)


# Utility Functions
def load_dataset(dataset_path: str = "data/longmemeval_s.json") -> pd.DataFrame:
    """Load the LongMemEval dataset from JSON file"""
    logger = logging.getLogger(__name__)
    logger.info(f"Loading dataset from {dataset_path}")

    # Check current directory first, then parent
    if os.path.exists(dataset_path):
        return pd.read_json(dataset_path)

    parent_path = os.path.join("..", os.path.basename(dataset_path))
    if os.path.exists(parent_path):
        logger.info(f"Using dataset from parent directory: {parent_path}")
        return pd.read_json(parent_path)

    raise FileNotFoundError(f"Dataset not found at {dataset_path} or {parent_path}")
