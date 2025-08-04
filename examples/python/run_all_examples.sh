#!/bin/bash

# Simple script to run all ZEP Python examples
# Just runs each example and shows the results

echo "Running all ZEP Python examples..."
echo "=================================="

# Activate virtual environment
source venv/bin/activate

# List of all Python example files
examples=(
    "simple.py"
    "advanced.py"
    "user_example.py"
    "chat_history/memory.py"
    "chat_history/chat_history_shoe_purchase.py"
    "graph_example/conversations.py"
    "graph_example/entity_types.py"
    "graph_example/graph_example.py"
    "graph_example/tickets_example.py"
    "graph_example/user_graph_example.py"
)

# Special cases that need different handling
echo "Running openai-agents-sdk example (help only):"
python openai-agents-sdk/openai_agents_sdk_example.py --help
echo ""

echo "Skipping ecommerce-chainlit examples (removed - outdated)"
echo ""

# Run each example
for example in "${examples[@]}"; do
    echo "Running: $example"
    echo "------------------------"
    python "$example"
    echo ""
    echo "=========================="
    echo ""
done

echo "All examples completed!"