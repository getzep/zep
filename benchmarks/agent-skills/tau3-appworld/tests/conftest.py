import os
from pathlib import Path

os.environ.setdefault(
    "TAU2_DATA_DIR", str(Path(__file__).resolve().parents[4] / "tau2-bench" / "data")
)

for _name in (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "BRAINTRUST_API_KEY",
    "BRAINTRUST_API_URL",
):
    os.environ.pop(_name, None)
