import os
import json
from time import time

import pandas as pd
from dotenv import load_dotenv
from openai import AsyncOpenAI
import asyncio


async def zep_response(llm_client, context: str, question: str) -> str:
    system_prompt = """
        You are a helpful expert assistant answering questions from lme_experiment users based on the provided context.
        """

    prompt = f"""
    # CONTEXT:
    You have access to facts and entities from a conversation.

    Your task is to briefly answer the question. You are given the following context from the previous conversation. If you don't know how to answer the question, abstain from answering.
        <CONTEXT>
        {context}
        </CONTEXT>
        <QUESTION>
        {question}
        </QUESTION>

    Clarification:
    When interpreting memories, use the timestamp to determine when the described event happened, not when someone talked about the event.
    
    Example:
    
    Memory: (2023-03-15T16:33:00Z) I went to the vet yesterday.
    Question: What day did I go to the vet?
    Correct Answer: March 15, 2023
    Explanation:
    Even though the phrase says "yesterday," the timestamp shows the event was recorded as happening on March 15th. Therefore, the actual vet visit happened on that date, regardless of the word "yesterday" in the text.


    # APPROACH (Think step by step):
    1. First, examine all memories that contain information related to the question
    2. Examine the timestamps and content of these memories carefully
    3. Look for explicit mentions of dates, times, locations, or events that answer the question
    4. If the answer requires calculation (e.g., converting relative time references), show your work
    5. Formulate a precise, concise answer based solely on the evidence in the memories
    6. Double-check that your answer directly addresses the question asked
    7. Ensure your final answer is specific and avoids vague time references

    Context:

    {context}

    Question: {question}
    Answer:
    """

    response = await llm_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    result = response.choices[0].message.content or ""

    return result


async def process_qa(qa, search_result, oai_client):
    start = time()
    query = qa.get("question")
    gold_answer = qa.get("answer")

    zep_answer = await zep_response(oai_client, search_result.get("context"), query)

    duration_ms = (time() - start) * 1000

    return {
        "question": query,
        "answer": zep_answer,
        "golden_answer": gold_answer,
        "duration_ms": duration_ms,
    }


async def main():
    # Load environment variables
    load_dotenv()

    # Initialize OpenAI client
    oai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    test_data_df = pd.read_json("data/test_data.json")

    with open("data/zep_search_results.json", "r") as file:
        zep_search_results = json.load(file)

    # Get context for each question
    num_users = 10

    zep_responses = {}
    for group_idx in range(num_users):
        qa_set = test_data_df["qa"].iloc[group_idx]
        qa_set_filtered = [qa for qa in qa_set if qa.get("category") != 5]

        group_id = f"rivian_experiment_user_{group_idx}"
        search_results = zep_search_results.get(group_id)

        tasks = [
            process_qa(qa, search_result, oai_client)
            for qa, search_result in zip(qa_set_filtered, search_results, strict=True)
        ]

        responses = await asyncio.gather(*tasks)
        zep_responses[group_id] = responses

    os.makedirs("data", exist_ok=True)

    print(zep_responses)

    with open("data/zep_responses.json", "w") as f:
        json.dump(zep_responses, f, indent=2)
        print("Save search results")


if __name__ == "__main__":
    asyncio.run(main())
