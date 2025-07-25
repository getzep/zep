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
SUMMARY_MODEL = "gpt-4.1-mini"
DATA_PATH = "data"

# Summarization Prompt Templates
SUMMARIZE_THREAD_CONTEXT_PROMPT_SYSTEM = """Your task is to extract and summarize only the relevant information 
from the provided CONTEXT that will help answer the QUESTION.
Instructions:
- Focus on facts and entities that best help answer the QUESTION.
- Include the complete relevant history that helps answer what the user is currently asking, not just the final outcome
- When summarizing recommendations or decisions, preserve the chronological sequence of how events unfolded
- Pay attention to temporal information: recognize expired timeframes, current vs future facts, and time-sensitive elements.
- Prioritize currently relevant information over outdated facts, and note when timeframes have passed or are approaching
- If multiple options were discussed or recommended, include all of them with the progression of how decisions were made
- Include specific details like dates, preferences, requirements, and constraints
- Write as a single, clear paragraph in third person (refer to the user by name, not as "you")
- Keep summary concise, aiming for 60-90 words while including the most important details
- Use the same language as the conversation (English, Spanish, French, etc.)
- Do NOT include words like "Context:", "Summary:", or other labels
- Return ONLY the summary content, no prefixes or headers
"""

SUMMARIZE_THREAD_CONTEXT_PROMPT_USER = """
<QUESTION>
{question}
</QUESTION>

<Context>
# Facts: 
{facts}

# Entities: 
{entities}
</Context>
"""

CONTEXT_TEMPLATE_SUMMARY = """
<CONTEXT>
{summary}
</CONTEXT>
"""


# Context template for search results
CONTEXT_TEMPLATE = """
FACTS and ENTITIES represent relevant context to the current conversation.

# These are the most relevant facts for the conversation along with the datetime of the event that the fact refers to.
If a fact mentions something happening a week ago, then the datetime will be the datetime of last week and not the datetime
of when the fact was stated.
Timestamps in memories represent the actual time the event occurred, not the time the event was mentioned in a message.
    
<FACTS>
{facts}
</FACTS>

# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
{entities}
</ENTITIES>
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
