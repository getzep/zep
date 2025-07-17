import os
import json
from collections import defaultdict
from time import time

import requests
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from zep_cloud.client import AsyncZep
from zep_cloud import Message, EntityEdge, EntityNode
from openai import AsyncOpenAI
import asyncio

class Grade(BaseModel):
  is_correct: str = Field(description='CORRECT or WRONG')
  reasoning: str = Field(description='Explain why the answer is correct or incorrect.')

async def locomo_grader(llm_client, question: str, gold_answer: str, response: str) -> bool:
    system_prompt = """
        You are an expert grader that determines if answers to questions match a gold standard answer
        """

    ACCURACY_PROMPT = f"""
    Your task is to label an answer to a question as ’CORRECT’ or ’WRONG’. You williolw23 be given the following data:
        (1) a question (posed by one user to another user), 
        (2) a ’gold’ (ground truth) answer, 
        (3) a generated answer
    which you will score as CORRECT/WRONG.

    The point of the question is to ask about something one user should know about the other user based on their prior conversations.
    The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
    Question: Do you remember what I got the last time I went to Hawaii?
    Gold answer: A shell necklace
    The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT. 

    For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

    Now it’s time for the real question:
    Question: {question}
    Gold answer: {gold_answer}
    Generated answer: {response}

    First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG. 
    Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

    Just return the label CORRECT or WRONG in a json format with the key as "label".
    """

    response = await llm_client.beta.chat.completions.parse(
        model='gpt-4o-mini',
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": ACCURACY_PROMPT}],
        response_format=Grade,
        temperature=0,
    )
    result = response.choices[0].message.parsed

    return result.is_correct.strip().lower() == 'correct'


async def main():
    # Load environment variables
    load_dotenv()

    # Initialize OpenAI client
    oai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


    with open('data/zep_locomo_responses.json', 'r') as file:
        zep_locomo_responses = json.load(file)

    # Get context for each question
    num_users = 10
    score = 0

    zep_grades = defaultdict(list)
    for group_idx in range(num_users):
        group_id = f"locomo_experiment_user_{group_idx}"
        zep_responses = zep_locomo_responses.get(group_id)

        tasks = []
        for response in zep_responses:
            question = response.get('question')
            zep_answer = response.get('answer')
            gold_answer = response.get('golden_answer')
            if gold_answer is None:
                continue

            task = locomo_grader(oai_client, question, gold_answer, zep_answer)
            tasks.append((question, zep_answer, gold_answer, task))

        results = await asyncio.gather(*(task for _, _, _, task in tasks))

        for (question, zep_answer, gold_answer, _), grade in zip(tasks, results):
            zep_grades[group_id].append({
                'question': question,
                'answer': zep_answer,
                'golden_answer': gold_answer,
                'grade': grade
            })

            if grade:
                score += 1

    os.makedirs("data", exist_ok=True)

    print('SCORE: ', score / 1540)

    with open("data/zep_locomo_grades.json", "w") as f:
        json.dump(dict(zep_grades), f, indent=2)
        print('Save search results')





if __name__ == "__main__":
    asyncio.run(main())