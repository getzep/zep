import os
from zep_cloud.client import Zep
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Zep client
zep_client = Zep(api_key=os.getenv("ZEP_API_KEY"))

# Define context template
template = """
# USER SUMMARY
%{user_summary}

# REQUIREMENTS AND PREFERENCES
%{edges limit=4 types=[HAS_REQUIREMENT,PREFERS_NEIGHBORHOOD]}

# KEY ENTITIES
%{entities limit=3}

# EPISODES
%{episodes limit=2}
"""

# Create the context template. v4 identifies a template by its UUID and
# takes a name.
context_template = zep_client.context.create_template(
    name="requirements-and-preferences-1",
    template=template
)
print(f"Created context template {context_template.uuid_}")
