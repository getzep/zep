import os
import time
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

uuid_value = uuid.uuid4().hex[:4]
user_id = f"examples-v4-simple-{uuid_value}"
user = client.user.create(
    user_id=user_id,
    first_name=first_name,
    last_name=last_name,
    email=email,
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

last_task_id = None
for thread in threads:
    zep_thread = client.thread.create(user_uuid=user.uuid_)
    result = client.thread.add_messages(
        thread_uuid=zep_thread.uuid_,
        messages=[Message(**message) for message in thread],
    )
    last_task_id = result.task.uuid_ if result.task else last_task_id

if last_task_id:
    deadline = time.monotonic() + 1200
    next_status_report = time.monotonic() + 30
    while True:
        task = client.task.get(last_task_id)
        if task.status == "succeeded":
            break
        if task.status == "partial":
            print(f"Warning: task {last_task_id} ended with partial status: {task.error}")
            break
        if task.status == "failed":
            raise RuntimeError(f"Message ingestion task {last_task_id} ended with {task.status}")
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(f"Timed out waiting for task {last_task_id}")
        if now >= next_status_report:
            print(f"Task {last_task_id} status={task.status}; still waiting.")
            next_status_report = now + 30
        time.sleep(2)
