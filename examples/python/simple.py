import os
from dotenv import find_dotenv, load_dotenv
from zep_cloud import AddMessage
from zep_cloud.client import Zep

load_dotenv(dotenv_path=find_dotenv())
client = Zep(api_key=os.environ.get("ZEP_API_KEY"))

first_name = "John"
last_name = "Doe"
email = "john.doe@example.com"
zep_user_role = f"{first_name} {last_name}"
zep_assistant_role = "ShoeSalesSupportBot"
ignore_roles = []

user = client.user.create(
    first_name = first_name,
    last_name = last_name,
    email=email
)
print(f"Created user {user.uuid_}")

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

for messages in threads:
    thread = client.thread.create(user_uuid=user.uuid_)
    print(f"Created thread {thread.uuid_}")

    for m in messages:
        client.thread.add_messages(thread.uuid_, messages=[AddMessage(**m)])

