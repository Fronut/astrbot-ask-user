# astrbot_plugin_ask_user_tool

An AstrBot plugin that provides an `ask_user` LLM tool, allowing the AI agent to ask the user a question mid-conversation and wait for the user's response.

## How it works

When the LLM calls `ask_user`:

1. A message with a clickable link is sent to the chat
2. A browser tab opens automatically showing a response form
3. The user fills in their answer and submits
4. The answer is returned to the LLM, which continues the conversation

```
LLM → ask_user("Which file should I edit?")
     → opens http://127.0.0.1:xxxxx/ask/...
     → user types "config.yaml" and submits
     → LLM receives "config.yaml" and proceeds
```

## Installation

Copy this folder into AstrBot's plugin directory:

```
data/plugins/astrbot_plugin_ask_user_tool/
    main.py
    metadata.yaml
```

Then restart AstrBot. The `ask_user` tool will appear in the tool list.

## Requirements

- Python 3.10+ (stdlib only -- no pip dependencies)
- AstrBot

## License

MIT
