"""Attach Zep search and add-data tools to a CrewAI agent."""

import os

from crewai import Agent
from zep_cloud.client import Zep

from zep_crewai import create_add_data_tool, create_search_tool

zep = Zep(api_key=os.environ["ZEP_API_KEY"])
graph = zep.graph.create(name="Company knowledge")
agent = Agent(
    role="Knowledge assistant",
    goal="Search and record company knowledge.",
    backstory="You use Zep to retrieve and add company knowledge.",
    tools=[
        create_search_tool(zep, graph_uuid=graph.uuid_),
        create_add_data_tool(zep, graph_uuid=graph.uuid_),
    ],
)
print(agent)
