import os
import json


def save_checkpoint(path: str, data: dict):
    """Atomically write checkpoint data to disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_checkpoint(path: str) -> dict:
    """Load checkpoint data from disk."""
    with open(path, "r") as f:
        return json.load(f)


def delete_checkpoint(path: str):
    """Remove a checkpoint file after successful completion."""
    if os.path.exists(path):
        os.remove(path)
        print(f"✓ Checkpoint removed: {path}")
