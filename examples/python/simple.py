import os
import uuid
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

threads = [
    [
        {"name": zep_user_role, "role": "user", "content": "Help me find some new running shoes. Adidas are my favorite"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Can do! How about the Adidas Ultra Boost 21 for $100?"},
        {"name": zep_user_role, "role": "user", "content": "Sounds good to me."},
    ],
    [
        {"name": zep_user_role, "role": "user", "content": "I tried the Adidas ultra boost, and I no longer like Adidas. I want Puma."},
        {"name": zep_assistant_role, "role": "assistant", "content": "I see. Do you want to try the Puma Velocity Nitro 2?"},
        {"name": zep_user_role, "role": "user", "content": "I used to own the Velocity Nitro 2. What's another Puma Shoe I can try?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "I see. Do you want to try the Puma Deviate Nitro Elite?"},
        {"name": zep_user_role, "role": "user", "content": "Sure"},
    ]
]

for thread in threads:
    uuid_value = uuid.uuid4().hex[:4]
    thread_id = "thread-" + uuid_value
    
    client.thread.create(
        thread_id=thread_id,
        user_id=user_id
    )
    
    for m in thread:
        client.thread.add_messages(thread_id=thread_id, messages=[Message(**m)])

