<p align="center">
  <a href="https://squidfunk.github.io/mkdocs-material/">
    <img src="https://github.com/getzep/zep/blob/main/assets/zep-bot-square-200x200.png?raw=true" width="150" alt="Zep Logo">
  </a>
</p>

<h1 align="center">
Zep: A long-term memory store for LLM applications
</h1>

<p align="center">
  <a href="https://discord.gg/W8Kw6bsgXQ"><img
    src="https://dcbadge.vercel.app/api/server/W8Kw6bsgXQ?style=flat"
    alt="Chat on Discord"
  /></a>
  <a href="https://twitter.com/intent/follow?screen_name=zep_ai" target="_new"><img alt="Twitter Follow" src="https://img.shields.io/twitter/follow/zep_ai"></a>
  <a href="https://pypi.org/project/zep-python"><img alt="PyPI - Downloads" src="https://img.shields.io/pypi/dw/zep-python?label=pypi%20downloads"></a>
  <a href="https://www.npmjs.com/package/@getzep/zep-js"><img alt="@getzep/zep-js" src="https://img.shields.io/npm/dw/%40getzep/zep-js?label=npm%20downloads"></a>
  <img src="https://github.com/getzep/zep/actions/workflows/build-test.yml/badge.svg" alt="build/test" />
  <img
  src="https://github.com/getzep/zep/actions/workflows/golangci-lint.yml/badge.svg"
  alt="GoLangCI Lint"
  />
</p>

<p align="center">
<a href="https://docs.getzep.com/deployment/quickstart/">Quick Start</a> | 
<a href="https://docs.getzep.com/">Documentation</a> | 
<a href="https://docs.getzep.com/sdk/langchain/">LangChain Support</a> | 
<a href="https://discord.gg/W8Kw6bsgXQ">Discord</a><br />
<a href="https://www.getzep.com">www.getzep.com</a>
</p>
<p>&nbsp;</p>
<h2 align="center">Easily add relevant documents, chat history memory & rich user data to your LLM app's prompts.</h2>
<p>&nbsp;</p>
<p align="center">
  <a href="https://docs.getzep.com/sdk">
    <img src="https://github.com/getzep/zep/blob/main/assets/memory_search.png?raw=true" 
alt="Zep Chat History Search" width="370"
/>
  </a>
  <a href="https://docs.getzep.com/sdk">
    <img src="https://github.com/getzep/zep/blob/main/assets/doc_search.png?raw=true" width="370"
alt="Zep Document Search"
/>
  </a>
</p>
<p>&nbsp;</p>



### Vector Database with Hybrid Search
Populate your prompts with relevant documents and chat history. Rich metadata and JSONPath query filters offer a powerful hybrid search over texts.

### Batteries Included Embedding & Enrichment
- Automatically embed texts, or bring your own vectors. 
- Enrichment of chat histories with summaries, named entities, token counts. Use these as search filters.
- Associate your own metadata with documents & chat histories.

### Fast, low-latency APIs and stateless deployments
- Zep’s local embedding models and async enrichment ensure a snappy user experience. 
- Storing documents and history in Zep and not in memory enables stateless deployment.

### Python & TypeScript/JS SDKs, LangChain Support, Edge Deployment
- Python & TypeScript/JS SDKs for easy integration with your LLM app.
- LangChain and LangChain.js integration
- TypeScript/JS SDK supports edge deployment.

### Get Started

#### Install Server

Please see the [Zep Quick Start Guide](https://docs.getzep.com/deployment/quickstart/) for important configuration information.

```bash
docker compose up
```

Looking for <a href="https://docs.getzep.com/deployment">other deployment options</a>?

#### Install SDK

Please see the Zep [Develoment Guide](https://docs.getzep.com/sdk/) for important beta information and usage instructions.

```bash
pip install zep-python
```

**or**

```bash
npm i @getzep/zep-js
```
