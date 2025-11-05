import streamlit as st
import uuid
import asyncio
import nest_asyncio
import html
import base64
from datetime import datetime, timezone
from agents import *
import os
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep

# Enable nested event loops for Streamlit compatibility
nest_asyncio.apply()

# Load environment variables
load_dotenv()

# Load and encode Zep logo as base64
@st.cache_data
def get_zep_logo_base64():
    with open("assets/zep-logo.png", "rb") as f:
        return base64.b64encode(f.read()).decode()

zep_logo_base64 = get_zep_logo_base64()

# Store Zep API key in session state (we'll create clients as needed)
if "zep_api_key" not in st.session_state:
    st.session_state.zep_api_key = os.getenv("ZEP_API_KEY")

async def load_users_from_zep() -> list:
    """
    Load all users from Zep, ordered by most recent first.

    Returns:
        list: List of user objects from Zep
    """
    # Create a fresh AsyncZep client for this operation
    zep_client = AsyncZep(api_key=st.session_state.zep_api_key)

    try:
        # Get all users (ordered by most recent)
        users_response = await zep_client.user.list_ordered(page_size=100, page_number=1)
        users = users_response.users if hasattr(users_response, 'users') else []
        return users

    except Exception as e:
        import traceback
        traceback.print_exc()
        return []

async def create_new_user(first_name: str, last_name: str = "", email: str = "") -> str:
    """
    Create a new user in Zep.

    Args:
        first_name: User's first name
        last_name: User's last name (optional)
        email: User's email (optional)

    Returns:
        str: The newly created user ID
    """
    # Generate a unique user ID
    user_id = f"{first_name.lower()}_{uuid.uuid4().hex[:8]}"

    # Create a fresh AsyncZep client for this operation
    zep_client = AsyncZep(api_key=st.session_state.zep_api_key)

    try:
        await zep_client.user.add(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            email=email if email else f"{user_id}@example.com"
        )
        return user_id
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None

async def create_zep_thread(user_id: str) -> str:
    """
    Create a new Zep thread for a specific user.
    Assumes the user already exists in Zep.

    Args:
        user_id: The Zep user ID to create the thread for

    Returns:
        str: The newly created thread ID
    """
    # Generate a unique thread ID
    thread_id = str(uuid.uuid4())

    # Create a fresh AsyncZep client for this operation
    zep_client = AsyncZep(api_key=st.session_state.zep_api_key)

    try:
        await zep_client.thread.create(
            thread_id=thread_id,
            user_id=user_id
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
    return thread_id

# Initialize users list in session state
if "users" not in st.session_state:
    loaded_users = asyncio.run(load_users_from_zep())
    st.session_state.users = loaded_users

# Initialize current user ID in session state
if "current_user_id" not in st.session_state:
    # If we have users, set current to the first one (most recent)
    if st.session_state.users:
        st.session_state.current_user_id = st.session_state.users[0].user_id
    else:
        st.session_state.current_user_id = None

# Initialize the chat agent in session state (create a fresh AsyncZep client for it)
if "agent" not in st.session_state:
    # Create a fresh AsyncZep client for the agent to use
    agent_zep_client = AsyncZep(api_key=st.session_state.zep_api_key)
    st.session_state.agent = ChatAgent(agent_zep_client)

async def load_threads_from_zep(user_id: str) -> dict:
    """
    Load all threads and their messages from Zep for a specific user.

    Args:
        user_id: The Zep user ID to load threads for

    Returns:
        dict: Dictionary of threads with their messages
    """
    # Create a fresh AsyncZep client for this operation
    zep_client = AsyncZep(api_key=st.session_state.zep_api_key)

    threads = {}

    try:
        # Get all threads for the user
        user_threads = await zep_client.user.get_threads(user_id=user_id)

        # For each thread, load its messages
        for zep_thread in user_threads:
            thread_id = zep_thread.thread_id

            try:
                # Get messages for this thread
                messages_response = await zep_client.thread.get(thread_id=thread_id)

                # Convert Zep messages to our format
                messages = []
                if messages_response.messages:
                    for msg in messages_response.messages:
                        messages.append({
                            "role": msg.role,
                            "content": msg.content,
                            "timestamp": msg.created_at if hasattr(msg, 'created_at') else datetime.now()
                        })

                # Determine thread name (use first user message or default)
                thread_name = "New Chat"
                if messages:
                    for msg in messages:
                        if msg["role"] == "user":
                            thread_name = msg["content"][:50]  # Use first 50 chars of first user message
                            break

                # Add to threads dict
                threads[thread_id] = {
                    "id": thread_id,
                    "name": thread_name,
                    "created_at": zep_thread.created_at if hasattr(zep_thread, 'created_at') else datetime.now(),
                    "messages": messages,
                    "zep_thread_id": thread_id  # The thread_id IS the zep_thread_id
                }

            except Exception as e:
                pass

        return threads

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {}

# Initialize session state for threads and messages
if "threads" not in st.session_state:
    st.session_state.threads = {}

# Load threads for current user if we have one
if st.session_state.current_user_id and not st.session_state.threads:
    # Load all existing threads from Zep for the current user
    loaded_threads = asyncio.run(load_threads_from_zep(st.session_state.current_user_id))
    st.session_state.threads = loaded_threads

if "no_zep_responses" not in st.session_state:
    st.session_state.no_zep_responses = {}  # {thread_id: [{"role": "assistant", "content": "...", "timestamp": ...}, ...]}

if "display_mode" not in st.session_state:
    st.session_state.display_mode = "zep_only"  # Options: "both", "zep_only", "no_zep_only"

if "generating_responses" not in st.session_state:
    st.session_state.generating_responses = False

if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None

if "latency_metrics" not in st.session_state:
    st.session_state.latency_metrics = {}  # {thread_id: [{zep_retrieval_ms, no_zep_llm_ms, zep_llm_ms}, ...]}

if "current_thread_id" not in st.session_state:
    # If we have threads, set current to the most recent one
    if st.session_state.threads:
        def get_datetime_for_init(thread):
            created_at = thread["created_at"]
            if isinstance(created_at, str):
                from dateutil import parser
                dt = parser.parse(created_at)
            else:
                dt = created_at
            # Ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        most_recent_thread = max(
            st.session_state.threads.values(),
            key=get_datetime_for_init
        )
        st.session_state.current_thread_id = most_recent_thread["id"]
    else:
        st.session_state.current_thread_id = None

# Initialize pending_thread flag
if "pending_thread" not in st.session_state:
    st.session_state.pending_thread = False

# Page config
st.set_page_config(
    page_title="Zep Agent Memory example",
    layout="wide"
)

# Get the agent instance
agent = st.session_state.agent

def create_new_thread():
    """Prepare for a new chat thread (actual creation happens on first message)"""
    if not st.session_state.current_user_id:
        st.error("Please select or create a user first")
        return

    # Set up a pending thread state
    st.session_state.current_thread_id = "pending"
    st.session_state.pending_thread = True
    st.rerun()

def switch_thread(thread_id):
    """Switch to a different thread"""
    st.session_state.current_thread_id = thread_id
    st.session_state.pending_thread = False
    st.rerun()

def switch_user(user_id):
    """Switch to a different user and load their threads"""
    st.session_state.current_user_id = user_id
    st.session_state.pending_thread = False

    # Clear current threads and load threads for the new user
    st.session_state.threads = {}
    st.session_state.current_thread_id = None

    # Load threads for the new user
    loaded_threads = asyncio.run(load_threads_from_zep(user_id))
    st.session_state.threads = loaded_threads

    # Set current thread to most recent if any exist
    if loaded_threads:
        def get_datetime_for_sort(thread):
            created_at = thread["created_at"]
            if isinstance(created_at, str):
                from dateutil import parser
                dt = parser.parse(created_at)
            else:
                dt = created_at
            # Ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        most_recent_thread = max(loaded_threads.values(), key=get_datetime_for_sort)
        st.session_state.current_thread_id = most_recent_thread["id"]

    st.rerun()

def handle_new_user_creation():
    """Handle the creation of a new user"""
    # Store that we want to show the new user form
    st.session_state.show_new_user_form = True
    st.rerun()

def update_thread_name(thread_id, name):
    """Update thread name based on first message"""
    if thread_id in st.session_state.threads:
        st.session_state.threads[thread_id]["name"] = name

def get_current_thread():
    """Get the current active thread"""
    if st.session_state.current_thread_id == "pending":
        # Return a mock thread for pending state
        return {
            "id": "pending",
            "name": "New Chat",
            "messages": [],
            "zep_thread_id": None
        }
    return st.session_state.threads.get(st.session_state.current_thread_id, None)

def get_conversation_history(thread_id):
    """Get conversation history formatted for OpenAI API (excludes context messages)"""
    if thread_id not in st.session_state.threads:
        return []

    messages = st.session_state.threads[thread_id]["messages"]
    # Filter out context messages and take last 8 user/assistant messages
    conversation_messages = [msg for msg in messages if msg["role"] != "context"]
    return [
        {"role": msg["role"], "content": msg["content"]}
        for msg in conversation_messages[-8:]
    ]

def add_message_to_thread(thread_id, role, content):
    """Add a message to a thread"""
    if thread_id in st.session_state.threads:
        st.session_state.threads[thread_id]["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc)
        })

def add_no_zep_response(thread_id, content):
    """Add a non-Zep response to the thread's no_zep_responses list"""
    if thread_id not in st.session_state.no_zep_responses:
        st.session_state.no_zep_responses[thread_id] = []
    st.session_state.no_zep_responses[thread_id].append({
        "role": "assistant",
        "content": content,
        "timestamp": datetime.now(timezone.utc)
    })

def add_latency_metrics(thread_id, zep_retrieval_ms, no_zep_llm_ms, zep_llm_ms):
    """Add latency metrics for a response to the thread's latency_metrics list"""
    if thread_id not in st.session_state.latency_metrics:
        st.session_state.latency_metrics[thread_id] = []
    st.session_state.latency_metrics[thread_id].append({
        "zep_retrieval_ms": zep_retrieval_ms,
        "no_zep_llm_ms": no_zep_llm_ms,
        "zep_llm_ms": zep_llm_ms
    })

def normalize_whitespace(text):
    """Normalize excessive whitespace while preserving structure"""
    import re
    # Split into lines
    lines = text.split('\n')
    normalized_lines = []

    for line in lines:
        # Only keep lines that have non-whitespace content, or keep single empty lines
        normalized_lines.append(line.rstrip())  # Remove trailing whitespace from each line

    # Join lines and collapse all blank lines
    result = '\n'.join(normalized_lines)
    # Replace 2+ consecutive newlines with just 1 (remove all blank lines)
    result = re.sub(r'\n{2,}', '\n', result)

    return result.strip()

def escape_markdown_and_html(text):
    """Escape both HTML and markdown syntax to prevent markdown processing"""
    # First normalize excessive whitespace
    text = normalize_whitespace(text)
    # Then escape HTML special characters
    text = html.escape(text)
    # Then escape markdown special characters by replacing with HTML entities
    text = text.replace('#', '&#35;')
    text = text.replace('*', '&#42;')
    text = text.replace('_', '&#95;')
    text = text.replace('`', '&#96;')
    text = text.replace('[', '&#91;')
    text = text.replace(']', '&#93;')
    return text

def render_zep_response_box(zep_context, zep_response, metrics, include_cursor=False, show_comparison_styling=True):
    """Render the Zep response box with context and metrics

    Args:
        show_comparison_styling: If True, show border, colored background, and checkmark (for "both" mode).
                                If False, minimal styling for single-column display.
    """
    # Prepare context HTML
    context_html = ""
    if zep_context:
        escaped_context = escape_markdown_and_html(zep_context)
        context_html = f"""<details style="margin-bottom: 12px;" class="context-details">
<summary style="cursor: pointer; list-style: none; padding: 8px 12px; background: #f5f5f5; border-radius: 6px; display: flex; align-items: center; gap: 8px; font-size: 14px; color: #666; user-select: none; transition: background 0.2s;">
<span class="chevron" style="transition: transform 0.2s;">▼</span>
<span>🧠 Context from Zep (in addition to last 8 messages)</span>
</summary>
<div style="height: 300px; overflow-y: auto; padding: 8px; font-family: monospace; font-size: 12px; background: #f8f8f8; border: 1px solid #ddd; border-radius: 4px; margin: 8px 0 0 0; line-height: 1.4; width: 100%; box-sizing: border-box; white-space: pre-wrap; word-wrap: break-word;">{escaped_context}</div>
</details>"""
    else:
        context_html = """<details style="margin-bottom: 12px;" class="context-details">
<summary style="cursor: pointer; list-style: none; padding: 8px 12px; background: #f5f5f5; border-radius: 6px; display: flex; align-items: center; gap: 8px; font-size: 14px; color: #666; user-select: none; transition: background 0.2s;">
<span class="chevron" style="transition: transform 0.2s;">▼</span>
<span>🧠 No context available</span>
</summary>
</details>"""

    # Prepare latency HTML
    latency_html = ""
    if metrics:
        latency_parts = []
        if metrics.get("zep_llm_ms") is not None:
            latency_parts.append(f"<span style='color: #666;'>LLM response time: {metrics['zep_llm_ms']}ms</span>")
        if metrics.get("zep_retrieval_ms") is not None:
            latency_parts.append(f"""<span style='background-color: #d4edda; color: #155724; padding: 2px 8px; border-radius: 4px; font-weight: 500;'>Zep retrieval: {metrics['zep_retrieval_ms']}ms</span>
                <span class='info-tooltip'>
                    <span class='info-icon'>i</span>
                    <span class='tooltiptext'>The latency shown includes both the round-trip network time between your system and Zep, and the time it takes Zep to retrieve and return search results.<br><br>Advertised Zep latency metrics reflect the latency of Zep retrieval without network latency. Network latency can be reduced through methods like VPC peering or a private network connection for enterprise customers.<br><br>Additionally, methods for reducing non-network Zep latency can be found <a href='https://help.getzep.com/performance' target='_blank' style='color: #66b3ff; text-decoration: underline;'>here</a>.</span>
                </span>""")
        if latency_parts:
            latency_html = f'<div style="margin-top: 12px; display: flex; gap: 8px; align-items: center; font-size: 12px;">{" ".join(latency_parts)}</div>'
        else:
            # Show calculating placeholder during streaming
            latency_html = '<div style="margin-top: 12px;"><span style="font-size: 12px; color: #666;">⏳ Calculating latency...</span></div>'

    # Add cursor if needed
    response_text = zep_response + "|" if include_cursor else zep_response

    # Apply styling based on mode
    if show_comparison_styling:
        # "Both" mode: show border, green background, and checkmark
        outer_style = "border: 2px solid black; border-radius: 8px; padding: 16px; background-color: white; margin-bottom: 16px;"
        emoji_html = '<div style="flex-shrink: 0; font-size: 20px;">✅</div>'
        response_bg = "background-color: #e6f7e6;"
    else:
        # Single-column mode: no border, white background, no emoji
        outer_style = "padding: 0; background-color: white; margin-bottom: 16px;"
        emoji_html = ""
        response_bg = "background-color: white;"

    return f"""<div style="{outer_style}">
{context_html}
<div style="display: flex; align-items: flex-start; gap: 12px;">
{emoji_html}
<div style="flex: 1; {response_bg} padding: 12px; border-radius: 6px;">{response_text}</div>
</div>
{latency_html}
</div>"""

def render_no_zep_response_box(no_zep_response, metrics, include_cursor=False, show_comparison_styling=True):
    """Render the no-Zep response box with metrics

    Args:
        show_comparison_styling: If True, show border, colored background, and X emoji (for "both" mode).
                                If False, minimal styling for single-column display.
    """
    # Prepare latency HTML
    latency_html = ""
    if metrics and metrics.get("no_zep_llm_ms") is not None:
        latency_html = f'<div style="margin-top: 12px;"><span style="font-size: 12px; color: #666;">LLM response time: {metrics["no_zep_llm_ms"]}ms</span></div>'
    elif metrics:
        # Show calculating placeholder during streaming
        latency_html = '<div style="margin-top: 12px;"><span style="font-size: 12px; color: #666;">⏳ Calculating latency...</span></div>'

    # Add cursor if needed
    response_text = no_zep_response + "|" if include_cursor else no_zep_response

    # Apply styling based on mode
    if show_comparison_styling:
        # "Both" mode: show border, red background, and X emoji
        outer_style = "border: 2px solid black; border-radius: 8px; padding: 16px; background-color: white; margin-bottom: 16px;"
        emoji_html = '<div style="flex-shrink: 0; font-size: 20px;">❌</div>'
        response_bg = "background-color: #ffe6e6;"
    else:
        # Single-column mode: no border, white background, no emoji
        outer_style = "padding: 0; background-color: white; margin-bottom: 16px;"
        emoji_html = ""
        response_bg = "background-color: white;"

    return f"""<div style="{outer_style}">
<details style="margin-bottom: 12px;" class="context-details">
<summary style="cursor: pointer; list-style: none; padding: 8px 12px; background: #f5f5f5; border-radius: 6px; display: flex; align-items: center; gap: 8px; font-size: 14px; color: #666; user-select: none; transition: background 0.2s;">
<span class="chevron" style="transition: transform 0.2s;">▼</span>
<span>📝 No context used, only last 8 messages</span>
</summary>
</details>
<div style="display: flex; align-items: flex-start; gap: 12px;">
{emoji_html}
<div style="flex: 1; {response_bg} padding: 12px; border-radius: 6px;">{response_text}</div>
</div>
{latency_html}
</div>"""

def render_context_block(context_content):
    """Render a context block with copy button"""
    return f'''<body style="margin: 0; padding: 0; background-color: white;">
    <div style="position: relative; height: 250px;">
        <button id="copyBtn" onclick="copyToClipboard()" style="position: absolute; top: 8px; right: 26px; background: rgba(255, 255, 255, 0.9); border: none; padding: 8px; cursor: pointer; display: flex; align-items: center; gap: 4px; color: #666; transition: all 0.2s; z-index: 10; border-radius: 4px; font-size: 11px;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
            <span id="btnText" style="display: none;"></span>
        </button>
        <div id="content" style="max-height: 250px; overflow-y: auto; font-family: monospace; white-space: pre-wrap; padding: 0 0 0 10px; background-color: white; margin: 0; height: 100%;">{escape_markdown_and_html(context_content)}</div>
    </div>
    <script>
        function copyToClipboard() {{
            const content = document.getElementById('content').innerText;
            const btn = document.getElementById('copyBtn');
            const btnText = document.getElementById('btnText');

            navigator.clipboard.writeText(content).then(() => {{
                btnText.textContent = 'Copied!';
                btnText.style.display = 'inline';
                btn.style.color = '#4CAF50';
                btn.style.background = 'rgba(76, 175, 80, 0.1)';

                setTimeout(() => {{
                    btnText.style.display = 'none';
                    btn.style.color = '#666';
                    btn.style.background = 'rgba(255, 255, 255, 0.9)';
                }}, 1500);
            }}).catch(err => {{
                console.error('Failed to copy:', err);
            }});
        }}

        // Hover effect
        const btn = document.getElementById('copyBtn');
        btn.addEventListener('mouseenter', () => {{
            btn.style.background = 'rgba(245, 245, 245, 0.95)';
        }});
        btn.addEventListener('mouseleave', () => {{
            if (document.getElementById('btnText').style.display === 'none') {{
                btn.style.background = 'rgba(255, 255, 255, 0.9)';
            }}
        }});
    </script>
    </body>'''

def get_no_zep_responses(thread_id):
    """Get the list of no-Zep responses for a thread"""
    return st.session_state.no_zep_responses.get(thread_id, [])

def display_messages_by_mode(thread, display_mode):
    """Display thread messages based on the selected display mode"""
    thread_id = thread["id"]
    zep_messages = thread["messages"]  # List of messages with role: user/context/assistant
    no_zep_responses = get_no_zep_responses(thread_id)  # List of assistant responses only
    latency_metrics = st.session_state.latency_metrics.get(thread_id, [])  # List of latency metrics

    # Build a list of user messages and their corresponding responses
    user_message_indices = [i for i, msg in enumerate(zep_messages) if msg["role"] == "user"]

    for idx, user_idx in enumerate(user_message_indices):
        # Display user message
        user_msg = zep_messages[user_idx]

        # Get corresponding Zep context and response (if they exist)
        zep_context = None
        zep_response = None

        # Check if next message is context (new format) or assistant (historical format)
        if user_idx + 1 < len(zep_messages):
            next_msg = zep_messages[user_idx + 1]
            if next_msg["role"] == "context":
                zep_context = next_msg["content"]
                # Assistant response should be at user_idx + 2
                if user_idx + 2 < len(zep_messages) and zep_messages[user_idx + 2]["role"] == "assistant":
                    zep_response = zep_messages[user_idx + 2]["content"]
            elif next_msg["role"] == "assistant":
                # Historical format: no context, assistant directly after user
                zep_response = next_msg["content"]

        # Get corresponding no-Zep response (if it exists)
        no_zep_response = no_zep_responses[idx]["content"] if idx < len(no_zep_responses) else None

        # Get corresponding latency metrics (if they exist)
        metrics = latency_metrics[idx] if idx < len(latency_metrics) else None

        # Always display user message - right-aligned with gray background
        st.markdown(f"""
        <div style="display: flex; justify-content: flex-end; margin-bottom: 16px;">
            <div style="max-width: 70%; background-color: #f0f0f0; border-radius: 18px; padding: 12px 16px;">
                {user_msg["content"]}
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Display responses based on mode
        if display_mode == "both":
            # Show side-by-side comparison
            col_left, col_right = st.columns(2)

            with col_left:
                st.markdown("<h4 style='text-align: center;'>Without Zep</h4>", unsafe_allow_html=True)

                if no_zep_response:
                    st.markdown(render_no_zep_response_box(no_zep_response, metrics, show_comparison_styling=True), unsafe_allow_html=True)
                else:
                    # Show placeholder when no non-Zep response exists
                    st.markdown(render_no_zep_response_box("<em>No response without Zep was generated or saved for this message.</em>", None, show_comparison_styling=True), unsafe_allow_html=True)

            with col_right:
                st.markdown(f"<h4 style='text-align: center; display: flex; align-items: flex-end; justify-content: center; gap: 8px;'>With Zep <img src='data:image/png;base64,{zep_logo_base64}' style='height: 28px; width: auto;'/></h4>", unsafe_allow_html=True)

                if zep_response:
                    st.markdown(render_zep_response_box(zep_context, zep_response, metrics, show_comparison_styling=True), unsafe_allow_html=True)

        elif display_mode == "zep_only":
            # Show only Zep version - minimal styling (no border, no emoji, white background)
            if zep_response:
                st.markdown(render_zep_response_box(zep_context, zep_response, metrics, show_comparison_styling=False), unsafe_allow_html=True)

        elif display_mode == "no_zep_only":
            # Show only no-Zep version - minimal styling (no border, no emoji, white background)
            if no_zep_response:
                st.markdown(render_no_zep_response_box(no_zep_response, metrics, show_comparison_styling=False), unsafe_allow_html=True)
            else:
                # Show placeholder when no non-Zep response exists
                st.markdown(render_no_zep_response_box("<em>No response without Zep was generated or saved for this message.</em>", None, show_comparison_styling=False), unsafe_allow_html=True)

# Custom CSS for ChatGPT-style sidebar and context expanders
st.markdown("""
<style>
    /* Hide default Streamlit button styling */
    .stButton button {
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        padding: 8px 12px !important;
        border-radius: 8px !important;
        text-align: left !important;
        transition: background-color 0.2s ease !important;
    }

    /* Thread list items - no outline, hover effect only */
    .stButton button[kind="secondary"] {
        color: inherit !important;
    }

    .stButton button:hover {
        background-color: rgba(128, 128, 128, 0.1) !important;
    }

    /* Active thread highlighting */
    .stButton button[disabled] {
        background-color: rgba(128, 128, 128, 0.15) !important;
        opacity: 1 !important;
    }

    /* Remove padding from context block expanders */
    div[data-testid="stExpanderDetails"] {
        padding: 0 !important;
    }
    div[data-testid="stExpanderDetails"] .stVerticalBlock {
        padding: 0 !important;
        gap: 0 !important;
    }
    div[data-testid="stExpanderDetails"] div[data-testid="stElementContainer"] {
        padding: 0 0 0 10px !important;
    }

    /* Remove border/outline from expanders */
    details[data-testid="stExpander"] {
        border: none !important;
    }
    div[data-testid="stExpander"] {
        border: none !important;
        box-shadow: none !important;
    }

    /* Tooltip styles */
    .info-tooltip {
        position: relative;
        display: inline-block;
        margin-left: 4px;
    }

    .info-tooltip .info-icon {
        cursor: help;
        color: #666;
        font-size: 11px;
        border: 1px solid #999;
        border-radius: 50%;
        width: 14px;
        height: 14px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-style: normal;
    }

    .info-tooltip .tooltiptext {
        visibility: hidden;
        width: 320px;
        background-color: #333;
        color: #fff;
        text-align: left;
        border-radius: 6px;
        padding: 12px;
        position: absolute;
        z-index: 1000;
        bottom: 125%;
        left: 50%;
        margin-left: -160px;
        opacity: 0;
        transition: opacity 0.3s;
        font-size: 12px;
        line-height: 1.5;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }

    .info-tooltip .tooltiptext::after {
        content: "";
        position: absolute;
        top: 100%;
        left: 50%;
        margin-left: -5px;
        border-width: 5px;
        border-style: solid;
        border-color: #333 transparent transparent transparent;
    }

    .info-tooltip:hover .tooltiptext {
        visibility: visible;
        opacity: 1;
    }

    /* Context details styling */
    .context-details summary:hover {
        background: #e8e8e8 !important;
    }

    .context-details[open] .chevron {
        transform: rotate(180deg);
    }
</style>
""", unsafe_allow_html=True)

# Create layout with sidebar and main chat
with st.sidebar:
    # Display mode toggle at the top (disabled during response generation)
    st.selectbox(
        "Display Mode",
        options=["both", "zep_only", "no_zep_only"],
        format_func=lambda x: {
            "both": "📊 Both Responses",
            "zep_only": "🧠 With Zep",
            "no_zep_only": "📝 Without Zep"
        }[x],
        key="display_mode_selector",
        index=["both", "zep_only", "no_zep_only"].index(st.session_state.display_mode),
        on_change=lambda: setattr(st.session_state, 'display_mode', st.session_state.display_mode_selector),
        disabled=st.session_state.generating_responses
    )

    st.markdown("---")

    # Top section: User selection and new thread button
    # Show new user form if requested
    if st.session_state.get("show_new_user_form", False):
        with st.form("new_user_form"):
            st.subheader("Create New User")
            first_name = st.text_input("First Name*", key="new_user_first_name")
            last_name = st.text_input("Last Name", key="new_user_last_name")
            email = st.text_input("Email", key="new_user_email")

            col1, col2 = st.columns(2)
            with col1:
                submit = st.form_submit_button("Create")
            with col2:
                cancel = st.form_submit_button("Cancel")

            if submit and first_name:
                # Create the new user
                new_user_id = asyncio.run(create_new_user(first_name, last_name, email))
                if new_user_id:
                    # Reload users list
                    st.session_state.users = asyncio.run(load_users_from_zep())
                    # Set as current user and prepare for a pending thread
                    st.session_state.current_user_id = new_user_id
                    st.session_state.threads = {}
                    st.session_state.current_thread_id = "pending"
                    st.session_state.pending_thread = True
                    st.session_state.show_new_user_form = False
                    st.rerun()
            elif cancel:
                st.session_state.show_new_user_form = False
                st.rerun()

    # User selection dropdown with "New User" as first option
    if st.session_state.users:
        # Create user ID list with "New User" option at the top
        user_options = ["➕ New User"] + [user.user_id for user in st.session_state.users]

        # Find current user index (add 1 to account for "New User" option)
        current_user_index = 0
        if st.session_state.current_user_id:
            for i, user in enumerate(st.session_state.users):
                if user.user_id == st.session_state.current_user_id:
                    current_user_index = i + 1  # +1 because "New User" is at index 0
                    break

        selected_option = st.selectbox(
            "User",
            options=user_options,
            index=current_user_index,
            key="user_selector",
            label_visibility="collapsed"
        )

        # Handle selection
        if selected_option == "➕ New User":
            handle_new_user_creation()
        elif selected_option != st.session_state.current_user_id:
            switch_user(selected_option)
    else:
        # No users exist - show only "New User" option
        st.selectbox(
            "User",
            options=["➕ New User"],
            index=0,
            key="user_selector_empty",
            label_visibility="collapsed",
            on_change=handle_new_user_creation
        )

    # New thread button
    if st.button("➕ New Thread", key="new_thread_icon", use_container_width=True):
        create_new_thread()

    st.markdown("---")

    # Get all threads sorted by creation time (newest first)
    def get_datetime(thread):
        created_at = thread["created_at"]
        if isinstance(created_at, str):
            from dateutil import parser
            dt = parser.parse(created_at)
        else:
            dt = created_at
        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    sorted_threads = sorted(
        st.session_state.threads.values(),
        key=get_datetime,
        reverse=True
    )

    # Display threads (don't display pending threads)
    for thread in sorted_threads:
        thread_id = thread["id"]
        if thread_id == "pending":
            continue

        thread_name = thread["name"]
        is_current = thread_id == st.session_state.current_thread_id

        # Truncate thread name for sidebar display
        display_name = thread_name[:40] + "..." if len(thread_name) > 40 else thread_name

        # Style current thread differently
        button_type = "primary" if is_current else "secondary"

        if st.button(
            display_name,
            key=f"thread_{thread_id}",
            use_container_width=True,
            type=button_type,
            disabled=is_current
        ):
            switch_thread(thread_id)

# Main chat interface
current_thread = get_current_thread()

if not st.session_state.current_user_id:
    # No user selected
    st.info("Please create or select a user to start chatting.")
elif current_thread:
    # Display chat messages from current thread using the selected display mode
    display_messages_by_mode(current_thread, st.session_state.display_mode)

    # Auto-scroll to bottom after messages are displayed
    st.components.v1.html("""
        <script>
            window.parent.document.querySelector('section.main').scrollTo({
                top: window.parent.document.querySelector('section.main').scrollHeight,
                behavior: 'smooth'
            });
        </script>
    """, height=0)

    # Chat input
    if prompt := st.chat_input("Type your message here..."):
        # Store the prompt and set the generating flag
        st.session_state.pending_prompt = prompt
        st.session_state.generating_responses = True
        st.rerun()

    # Process pending prompt if we have one
    if st.session_state.pending_prompt and st.session_state.generating_responses:
        prompt = st.session_state.pending_prompt
        st.session_state.pending_prompt = None  # Clear it so we don't process again

        # Track if we're creating a new thread
        thread_just_created = False

        # If this is a pending thread, create it now
        if st.session_state.current_thread_id == "pending":
            zep_thread_id = asyncio.run(create_zep_thread(st.session_state.current_user_id))

            # Create the thread in session state
            thread_name = prompt[:50]  # Use first 50 chars of first message as name
            st.session_state.threads[zep_thread_id] = {
                "id": zep_thread_id,
                "name": thread_name,
                "created_at": datetime.now(timezone.utc),
                "messages": [],
                "zep_thread_id": zep_thread_id
            }
            st.session_state.current_thread_id = zep_thread_id
            st.session_state.pending_thread = False
            current_thread = st.session_state.threads[zep_thread_id]
            thread_just_created = True

        # Add user message to current thread
        add_message_to_thread(st.session_state.current_thread_id, "user", prompt)

        # Display user message immediately - right-aligned with gray background
        st.markdown(f"""
        <div style="display: flex; justify-content: flex-end; margin-bottom: 16px;">
            <div style="max-width: 70%; background-color: #f0f0f0; border-radius: 18px; padding: 12px 16px;">
                {prompt}
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Scroll to bottom after displaying user message
        st.components.v1.html("""
            <script>
                window.parent.document.querySelector('section.main').scrollTo({
                    top: window.parent.document.querySelector('section.main').scrollHeight,
                    behavior: 'smooth'
                });
            </script>
        """, height=0)

        # Get conversation history for the agent
        conversation_history = get_conversation_history(st.session_state.current_thread_id)

        # Generate and stream response using the agent
        try:
            # Use the Zep thread ID
            zep_thread_id = current_thread["zep_thread_id"]

            # Get current user's name
            current_user = next((user for user in st.session_state.users if user.user_id == st.session_state.current_user_id), None)
            user_full_name = f"{current_user.first_name} {current_user.last_name}".strip()

            # Get current display mode
            current_display_mode = st.session_state.display_mode

            # Create a fresh agent with a new AsyncZep client to avoid event loop issues
            fresh_zep_client = AsyncZep(api_key=st.session_state.zep_api_key)
            agent = ChatAgent(fresh_zep_client)

            # Create layout based on display mode
            if current_display_mode == "both":
                col_left, col_right = st.columns(2)
                with col_left:
                    st.markdown("<h4 style='text-align: center;'>Without Zep</h4>", unsafe_allow_html=True)
                    no_zep_response_placeholder = st.empty()
                    # Show loading indicator
                    no_zep_response_placeholder.markdown("""
                    <div style="display: flex; align-items: center; justify-content: center; padding: 40px; color: #666;">
                        <div style="text-align: center;">
                            <div style="font-size: 24px; margin-bottom: 8px;">⏳</div>
                            <div>Generating response...</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                with col_right:
                    st.markdown(f"<h4 style='text-align: center; display: flex; align-items: flex-end; justify-content: center; gap: 8px;'>With Zep <img src='data:image/png;base64,{zep_logo_base64}' style='height: 28px; width: auto;'/></h4>", unsafe_allow_html=True)
                    zep_response_placeholder = st.empty()
                    # Show loading indicator
                    zep_response_placeholder.markdown("""
                    <div style="display: flex; align-items: center; justify-content: center; padding: 40px; color: #666;">
                        <div style="text-align: center;">
                            <div style="font-size: 24px; margin-bottom: 8px;">⏳</div>
                            <div>Generating response...</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
            elif current_display_mode == "zep_only":
                zep_response_placeholder = st.empty()
                # Show loading indicator
                zep_response_placeholder.markdown("""
                <div style="display: flex; align-items: center; justify-content: center; padding: 40px; color: #666;">
                    <div style="text-align: center;">
                        <div style="font-size: 24px; margin-bottom: 8px;">⏳</div>
                        <div>Generating response...</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                # Create dummy placeholder for no_zep (won't be displayed)
                no_zep_response_placeholder = None
            else:  # no_zep_only
                no_zep_response_placeholder = st.empty()
                # Show loading indicator
                no_zep_response_placeholder.markdown("""
                <div style="display: flex; align-items: center; justify-content: center; padding: 40px; color: #666;">
                    <div style="text-align: center;">
                        <div style="font-size: 24px; margin-bottom: 8px;">⏳</div>
                        <div>Generating response...</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                # Create dummy placeholder for zep (won't be displayed)
                zep_response_placeholder = None

            # Async function to stream both responses in parallel
            async def stream_both_responses():
                # Create both generators
                generator_no_zep = agent.on_receive_message(prompt, conversation_history, zep_thread_id, user_full_name, st.session_state.current_user_id, use_zep=False)
                generator_with_zep = agent.on_receive_message(prompt, conversation_history, zep_thread_id, user_full_name, st.session_state.current_user_id, use_zep=True)

                # Get context blocks (first yield from each generator) - now returns tuples
                context_no_zep, _ = await generator_no_zep.__anext__()
                context_with_zep, _ = await generator_with_zep.__anext__()

                # We'll render the entire boxes during streaming, so no need for separate context placeholders

                # Determine if we should use comparison styling
                use_comparison_styling = (current_display_mode == "both")

                # Stream both assistant responses in parallel using tasks
                async def stream_no_zep():
                    response = ""
                    timing = {}
                    async for token, timing_data in generator_no_zep:
                        if timing_data:
                            timing = timing_data
                        if token:
                            response += token
                            # Only display if placeholder exists
                            if no_zep_response_placeholder:
                                # Render with cursor and calculating latency placeholder
                                streaming_metrics = {"no_zep_llm_ms": None}  # Placeholder during streaming
                                with no_zep_response_placeholder.container():
                                    st.markdown(render_no_zep_response_box(response, streaming_metrics, include_cursor=True, show_comparison_styling=use_comparison_styling), unsafe_allow_html=True)

                    # Final render without cursor and with actual latency
                    if no_zep_response_placeholder:
                        final_metrics = {"no_zep_llm_ms": timing.get("llm_first_token_ms")}
                        with no_zep_response_placeholder.container():
                            st.markdown(render_no_zep_response_box(response, final_metrics, show_comparison_styling=use_comparison_styling), unsafe_allow_html=True)

                    return response, timing

                async def stream_with_zep():
                    response = ""
                    timing = {}

                    async for token, timing_data in generator_with_zep:
                        if timing_data:
                            timing = timing_data
                        if token:
                            response += token
                            # Only display if placeholder exists
                            if zep_response_placeholder:
                                # Render with cursor and placeholder metrics
                                streaming_metrics = {"zep_llm_ms": None, "zep_retrieval_ms": None}
                                with zep_response_placeholder.container():
                                    st.markdown(render_zep_response_box(context_with_zep, response, streaming_metrics, include_cursor=True, show_comparison_styling=use_comparison_styling), unsafe_allow_html=True)

                    # Final render without cursor and with actual latency
                    if zep_response_placeholder:
                        final_metrics = {
                            "zep_llm_ms": timing.get("llm_first_token_ms"),
                            "zep_retrieval_ms": timing.get("zep_retrieval_ms")
                        }
                        with zep_response_placeholder.container():
                            st.markdown(render_zep_response_box(context_with_zep, response, final_metrics, show_comparison_styling=use_comparison_styling), unsafe_allow_html=True)

                    return response, timing

                # Run both streams in parallel
                (response_no_zep, timing_no_zep), (response_with_zep, timing_with_zep) = await asyncio.gather(
                    stream_no_zep(),
                    stream_with_zep()
                )

                return context_with_zep, response_with_zep, response_no_zep, timing_no_zep, timing_with_zep

            context_block, full_response, no_zep_response, timing_no_zep, timing_with_zep = asyncio.run(stream_both_responses())

            # Add context and assistant response to current thread (Zep version only)
            add_message_to_thread(st.session_state.current_thread_id, "context", context_block)
            add_message_to_thread(st.session_state.current_thread_id, "assistant", full_response)

            # Store the no-Zep response in separate state (not persisted to Zep)
            add_no_zep_response(st.session_state.current_thread_id, no_zep_response)

            # Store latency metrics
            add_latency_metrics(
                st.session_state.current_thread_id,
                zep_retrieval_ms=timing_with_zep.get("zep_retrieval_ms"),
                no_zep_llm_ms=timing_no_zep.get("llm_first_token_ms"),
                zep_llm_ms=timing_with_zep.get("llm_first_token_ms")
            )

            # Reset the generating flag to re-enable display mode selector
            st.session_state.generating_responses = False

            # Scroll to bottom after streaming completes
            st.components.v1.html("""
                <script>
                    window.parent.document.querySelector('section.main').scrollTo({
                        top: window.parent.document.querySelector('section.main').scrollHeight,
                        behavior: 'smooth'
                    });
                </script>
            """, height=0)

            # Rerun to update the UI (either for new thread or to re-enable display mode selector)
            st.rerun()

        except Exception as e:
            import traceback
            traceback.print_exc()
            error_message = f"Error: {str(e)}"

            # Reset the generating flag even on error
            st.session_state.generating_responses = False

            with st.chat_message("assistant"):
                st.markdown(error_message)
                add_message_to_thread(st.session_state.current_thread_id, "assistant", error_message)

            # Rerun to re-enable display mode selector
            st.rerun()
else:
    # User selected but no threads
    st.info("Click '🖊️ New Chat' to start a conversation.")