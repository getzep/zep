"""Create and use a shared Zep graph."""

import os

from zep_cloud.client import Zep

from zep_crewai import ZepGraphStorage, create_search_tool

zep = Zep(api_key=os.environ["ZEP_API_KEY"])
graph = zep.graph.create(name="Company knowledge")
storage = ZepGraphStorage(zep, graph_uuid=graph.uuid_)
storage.save("Project Atlas uses Python.", metadata={"type": "text"})

tool = create_search_tool(zep, graph_uuid=graph.uuid_)
print(tool.run(query="What language does Project Atlas use?"))
