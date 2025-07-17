import os
import json
from collections import defaultdict
from time import time

import requests
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep
from zep_cloud import Message, EntityEdge, EntityNode
from openai import AsyncOpenAI
import asyncio

TEMPLATE = """
FACTS and ENTITIES represent relevant context to the current conversation.

# These are the most relevant facts for the conversation along with the datetime of the event that the fact refers to.
If a fact mentions something happening a week ago, then the datetime will be the date time of last week and not the datetime
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

def compose_search_context(edges: list[EntityEdge], nodes: list[EntityNode]) -> str:
    facts = [f'  - {edge.fact} (event_time: {edge.valid_at})' for edge in edges]
    entities = [f'  - {node.name}: {node.summary}' for node in nodes]
    return TEMPLATE.format(facts='\n'.join(facts), entities='\n'.join(entities))


async def main():
    # Load environment variables
    load_dotenv()

    # Initialize Zep client
    zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"), base_url="https://api.getzep.com/api/v2")

    # Download JSON data
    url = "https://raw.githubusercontent.com/snap-research/locomo/refs/heads/main/data/locomo10.json"
    response = requests.get(url)
    locomo_df = pd.read_json(url)

    # Get context for each question
    num_users = 10

    zep_search_results = defaultdict(list)
    for group_idx in range(num_users):
        qa_set = locomo_df['qa'].iloc[group_idx]
        group_id = f"locomo_experiment_user_{group_idx}"

        for qa in qa_set:
            start = time()
            query = qa.get('question')
            if qa.get('category') == 5:
                continue

            search_results = await asyncio.gather(
                zep.graph.search(query=query, group_id=group_id, scope='nodes', reranker='rrf', limit=20),
                zep.graph.search(query=query, group_id=group_id, scope='edges', reranker='cross_encoder', limit=20))

            nodes = search_results[0].nodes
            edges = search_results[1].edges

            context = compose_search_context(edges, nodes)
            duration_ms = (time() - start) * 1000

            zep_search_results[group_id].append({'context': context, 'duration_ms': duration_ms})

    os.makedirs("data", exist_ok=True)

    print(zep_search_results)

    with open("data/zep_locomo_search_results.json", "w") as f:
        json.dump(dict(zep_search_results), f, indent=2)
        print('Save search results')





if __name__ == "__main__":
    asyncio.run(main())