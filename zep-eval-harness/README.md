# Zep Eval Harness

An end-to-end evaluation framework for testing Zep's memory retrieval and question-answering capabilities using dummy datasets.

## Quick Start

1. **Modify the conversations and test questions in the data folder to match your use case**
   - Edit `data/conversations.json` to add your conversation data
   - Edit `data/test_questions.csv` to add your test queries and evaluation criteria

2. **Install requirements**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set API keys**
   - Copy `.env.example` to `.env`: `cp .env.example .env`
   - Get your Zep API key: https://app.getzep.com
   - Get your OpenAI API key: https://platform.openai.com/api-keys
   - Add both keys to your `.env` file

4. **Run ingestion script** (one-time setup)
   ```bash
   python zep_ingest.py
   ```

5. **Run evaluation script** (can run multiple times)
   ```bash
   python zep_evaluate.py
   ```

## Overview

This harness evaluates the complete Zep-powered QA pipeline in just **two scripts**:

### Architecture

```
data/conversations.json → [zep_ingest.py] → Zep Cloud Knowledge Graph
                                                    ↓
data/test_questions.csv → [zep_evaluate.py] → Search → Generate Response → Grade
                                                    ↓
                                          zep_evaluation_results.json
```

### Pipeline Steps (automated in zep_evaluate.py)

1. **Search**: Query Zep's knowledge graph (episodes, nodes, edges)
2. **Generate Response**: Use GPT-4o-mini with retrieved context to answer questions
3. **Grade**: Evaluate answers against golden criteria using an LLM judge

## Advanced Evaluation

### Tune Zep Search Parameters and Context Block

It's worth experimenting with different Zep search strategies and context blocks to optimize retrieval for your specific use case. You can customize search parameters like rerankers, search scopes, and result limits, as well as how the retrieved context is formatted before being sent to the LLM. For guidance, check out the [Searching the Graph documentation](https://help.getzep.com/searching-the-graph) and the [Customize Your Context Block documentation](https://help.getzep.com/cookbook/customize-your-context-block).

### Add JSON Data

JSON and unstructured text can also be added to user graphs, not just conversation data. These could represent documents, transcripts, emails, user interactions, user business data, and more. Adding diverse data types can help test how well Zep retrieves information across different content formats. For more information, see the [Adding Data to the Graph documentation](https://help.getzep.com/adding-data-to-the-graph).

### Create a Large Background Graph to Test Long-term Retrieval

You can test long-term retrieval by modifying the ingestion script to ingest a fixed/large amount of background data first, before adding your conversation data. This tests Zep's retrieval capabilities when there is a larger haystack to retrieve the needles from. Additionally, this large background graph can be created a single time and then cloned afterwards using the graph clone method that Zep provides, before adding the use case specific conversation data. This approach saves time when running multiple evaluations. For more information on cloning graphs, see the [Cloning Graphs documentation](https://help.getzep.com/adding-data-to-the-graph#cloning-graphs).

## Best Practices for Fair Tests

To ensure reliable and meaningful evaluation results, follow these best practices when designing your test questions and evaluation criteria:

### 1. Ensure Answer Availability
The answer to each test question must be present somewhere in the conversation history. Tests become unfair when they expect the AI to answer questions about information that was never discussed or provided.

### 2. Align Gold Answer Criteria with Test Questions
The gold answer criteria should only require information that directly addresses what the test question asks for. Avoid including extraneous information beyond the scope of the question.

**Example:**
- **Test Question**: "When is my appointment?"
- **Good Gold Criteria**: Must include the date and time of the appointment
- **Poor Gold Criteria**: Must include the date, time, and address of the appointment (address is beyond what was asked)

### 3. Write Unambiguous Test Questions
Ambiguous test questions can lead to varied or unintended responses from the AI if it cannot determine the intended meaning. Clear, specific questions produce more consistent and reliable evaluation results.

**Note:** While Zep's contextual memory can often correctly interpret and answer some ambiguous questions by leveraging conversation history, there is still a point at which the responsibility falls on the user to provide a less ambiguous question to the agent. The more specific and clear the question, the more reliable the results will be.

**Example of an ambiguous question:**
- "What did I order?" (ambiguous if multiple orders were discussed across different contexts or timeframes)

**Better alternatives:**
- "What did I order for lunch today?"
- "What items were in my last online purchase?"
- "What did I order at the restaurant on Monday?"

### 4. Consider Context and Scope
Ensure your test questions clearly specify any necessary context such as timeframes, locations, or specific instances when multiple similar events might exist in the conversation history.

