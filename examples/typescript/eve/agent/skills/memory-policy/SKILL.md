---
description: Use when deciding whether to search Zep memory for user preferences.
---

# Memory policy

- Conversation turns are auto-persisted to Zep; durable preferences stated in
  chat become graph facts without a separate save tool.
- Turn-scoped system instructions may include shallow Zep recall for the
  current message — call `zep_search` when that section is missing or incomplete.
- Never request or echo secrets (passwords, tokens, payment data, private keys, OTPs).
- When Service A vs Service B matters, check memory first, then call the matching tool.
