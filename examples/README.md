# Zep examples

Runnable examples for the public Zep Cloud SDKs.

| Language | Directory | SDK |
| --- | --- | --- |
| Python | [`python`](./python) | [`zep-cloud`](https://pypi.org/project/zep-cloud/) v4 |
| TypeScript | [`typescript`](./typescript) | [`@getzep/zep-cloud`](https://www.npmjs.com/package/@getzep/zep-cloud) v4 |
| Go | [`go`](./go) | [`github.com/getzep/zep-go/v4`](https://pkg.go.dev/github.com/getzep/zep-go/v4) |

The examples use published SDK packages. They do not require a local SDK checkout.

## Agent harnesses

An agent harness is an agent that runs with Zep. Each harness has its own README
with setup and run commands.

### Context Lake harnesses

These agents get context from Context Graphs in the
[Context Lake](https://help.getzep.com/context-lake). They retrieve user memory,
business data, or domain knowledge at task time.

Python:

| Harness | Framework | What it shows |
| --- | --- | --- |
| [`agent-with-zep`](./python/agent-with-zep) | PydanticAI | A reference agent that plans, then retrieves from a Context Graph with tools, and an evaluation script |
| [`agent-with-zep-adk`](./python/agent-with-zep-adk) | Google ADK | The same reference agent on Google ADK |
| [`langgraph-agent`](./python/langgraph-agent) | LangGraph | A notebook agent that adds chat turns to Zep and uses `thread.get_context` and graph search tools |
| [`autogen-agent`](./python/autogen-agent) | AutoGen | A notebook agent that recalls facts from an earlier conversation |
| [`openai-agents-sdk`](./python/openai-agents-sdk) | OpenAI Agents SDK | An assistant that remembers earlier conversations |
| [`elevenlabs-zep-example`](./python/elevenlabs-zep-example) | ElevenLabs Conversational AI | A voice agent that gets Zep context through a custom LLM proxy |
| [`user-summary-instructions-example`](./python/user-summary-instructions-example) | OpenAI client | A sales chatbot that uses user summary instructions |
| [`context-templates-example`](./python/context-templates-example) | OpenAI client | A chatbot that gets its context from a context template |
| [`zep-quickstart-dashboard`](./python/zep-quickstart-dashboard) | OpenAI client | A Streamlit dashboard that compares answers with and without Zep |
| [`quickstart`](./python/quickstart) | OpenAI client | A notebook that adds chat, CRM, and billing data, then runs a support chatbot |

TypeScript:

| Harness | Framework | What it shows |
| --- | --- | --- |
| [`langgraph`](./typescript/langgraph) | LangGraph.js | A CLI agent that keeps its memory in Zep |
| [`eve`](./typescript/eve) | Eve | An Eve agent that recalls user memory in each turn and searches with Zep tools |

### Agent Skills harnesses

These agents use [Zep Agent Skills](https://help.getzep.com/agent-skills). A
teacher agent records a Trace of a task. Zep compiles a skill from the verified
Trace. A student agent then finds the skill, reads it, and does a new task. All
three harnesses use the same invented banking task.

| Harness | Framework | What it shows |
| --- | --- | --- |
| [`python/agent-skills-banking`](./python/agent-skills-banking) | PydanticAI | The full loop, with a student run before and after the skill |
| [`python/agent-skills-frameworks`](./python/agent-skills-frameworks) | LangGraph, Google ADK, Claude Agent SDK | Trace capture from the hooks of each framework |
| [`typescript/agent-skills-frameworks`](./typescript/agent-skills-frameworks) | Mastra, OpenAI Codex SDK, Eve | Trace capture from the hooks of each framework |

## SDK snippets and tools

| Example | What it shows |
| --- | --- |
| [`python/graph_example`](./python/graph_example) | Graph, episode, ontology, and search calls in Python |
| [`typescript/graph`](./typescript/graph) | Graph, episode, ontology, and search calls in TypeScript |
| [`typescript/users`](./typescript/users) | User calls in TypeScript |
| [`python/chunking-example`](./python/chunking-example), [`typescript/chunking-example`](./typescript/chunking-example), [`go/chunking-example`](./go/chunking-example) | Contextualized chunking of a document before ingestion |
| [`typescript/zep-graph-visualization`](./typescript/zep-graph-visualization) | A Next.js app that shows a Context Graph |

## Toolchain

- Python 3.10 or newer. The Agent Skills harnesses need Python 3.12 or 3.13.
- Node.js 18 or newer. `typescript/eve` and `typescript/agent-skills-frameworks` need Node.js 24.
- `typescript/zep-graph-visualization` uses yarn. The other TypeScript examples use npm.
- Go 1.22 or newer.

## API keys

All examples need `ZEP_API_KEY`. Each example lists its other keys in its README
or `.env.example`. The usual other keys are `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
and `GOOGLE_API_KEY`.

Some examples replace project-level configuration and do not create scoped
resources. `go run . entity-types` and the equivalent Python and TypeScript
ontology examples overwrite the ontology for the whole project. Use a disposable
project key for these examples.

## Identifiers

v4 gives every user, thread, and graph a server-generated UUID. The UUID is
the address of the resource. A v4 create call accepts no client-chosen name,
so a new resource has no `user_id`, no `thread_id`, and no `graph_id`. The
examples create a resource, read the UUID from the response, and use that UUID
in all later calls.

## Archived examples

The [`archive`](./archive) directory contains examples that show the older
memory-layer pattern. These examples are not maintained. For new code, use the
tool-based reference agents in [`python/agent-with-zep`](./python/agent-with-zep)
and [`python/agent-with-zep-adk`](./python/agent-with-zep-adk).
