import os
import json
from collections import defaultdict

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
import asyncio


class Grade(BaseModel):
    is_correct: str = Field(description="CORRECT or WRONG")
    reasoning: str = Field(
        description="Explain why the answer is correct or incorrect."
    )


async def zep_grader(
    llm_client, question: str, gold_answer: str, response: str
) -> bool:
    system_prompt = """
        You are an expert grader that determines if answers to questions match a gold standard answer
        """

    ACCURACY_PROMPT = f"""
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
    """

    response = await llm_client.beta.chat.completions.parse(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": ACCURACY_PROMPT},
        ],
        response_format=Grade,
        temperature=0,
    )
    result = response.choices[0].message.parsed

    return result.is_correct.strip().lower() == "yes"


async def main():
    # Load environment variables
    load_dotenv()

    # Initialize OpenAI client
    oai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    with open("data/zep_responses.json", "r") as file:
        zep_responses = json.load(file)

    # Get context for each question
    num_users = 10
    score = 0

    zep_grades = defaultdict(list)
    for group_idx in range(num_users):
        group_id = f"rivian_experiment_user_{group_idx}"
        zep_responses = zep_responses.get(group_id)

        tasks = []
        for response in zep_responses:
            question = response.get("question")
            zep_answer = response.get("answer")
            gold_answer = response.get("golden_answer")
            if gold_answer is None:
                continue

            task = zep_grader(oai_client, question, gold_answer, zep_answer)
            tasks.append((question, zep_answer, gold_answer, task))

        results = await asyncio.gather(*(task for _, _, _, task in tasks))

        for (question, zep_answer, gold_answer, _), grade in zip(tasks, results):
            zep_grades[group_id].append(
                {
                    "question": question,
                    "answer": zep_answer,
                    "golden_answer": gold_answer,
                    "grade": grade,
                }
            )

            if grade:
                score += 1

    os.makedirs("data", exist_ok=True)

    with open("data/zep_grades.json", "w") as f:
        json.dump(dict(zep_grades), f, indent=2)
        print("Save search results")


if __name__ == "__main__":
    asyncio.run(main())
