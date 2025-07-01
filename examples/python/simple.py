import os
import uuid
import time
from dotenv import find_dotenv, load_dotenv
from zep_cloud.client import Zep
from zep_cloud.types import Message

load_dotenv(dotenv_path=find_dotenv())
client = Zep(api_key=os.environ.get("ZEP_API_KEY"))

first_name = "John"
last_name = "Doe"
email = "john.doe@example.com"
zep_user_role = f"{first_name} {last_name}"
zep_assistant_role = "ShoeSalesSupportBot"
ignore_roles = []

client.graph.set_entity_types(
    entities={}
)

uuid_value = uuid.uuid4().hex[:4]
user_id = "default-graph-simple-" + uuid_value
client.user.add(
    user_id=user_id,
    first_name = first_name,
    last_name = last_name,
    email=email
)

sessions = [
    [
        {"role": zep_user_role, "role_type": "user", "content": "Help me find some new running shoes. Adidas are my favorite"},
        {"role": zep_assistant_role, "role_type": "assistant", "content": "Can do! How about the Adidas Ultra Boost 21 for $100?"},
        {"role": zep_user_role, "role_type": "user", "content": "Sounds good to me."},
    ],
    [
        {"role": zep_user_role, "role_type": "user", "content": "I tried the Adidas ultra boost, and I no longer like Adidas. I want Puma."},
        {"role": zep_assistant_role, "role_type": "assistant", "content": "I see. Do you want to try the Puma Velocity Nitro 2?"},
        {"role": zep_user_role, "role_type": "user", "content": "I used to own the Velocity Nitro 2. What's another Puma Shoe I can try?"},
        {"role": zep_assistant_role, "role_type": "assistant", "content": "I see. Do you want to try the Puma Deviate Nitro Elite?"},
        {"role": zep_user_role, "role_type": "user", "content": "Sure"},
    ]
]

for session in sessions:
    uuid_value = uuid.uuid4().hex[:4]
    session_id = "session-" + uuid_value
    
    client.memory.add_session(
        session_id=session_id,
        user_id=user_id
    )
    
    for m in session:
        client.memory.add(session_id=session_id, messages=[Message(**m)], ignore_roles=ignore_roles)

