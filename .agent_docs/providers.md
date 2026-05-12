# LLM providers

Every provider posts JSON through `httpx.AsyncClient`; we deliberately avoid vendor SDKs to keep dependencies small and predictable.

## `openai-compatible`

`src/config_a2a/providers/openai_compat.py`. Required YAML fields: `model`, `base_url`, `api_key_env`. Forwards `Authorization: Bearer <key>` and any `extra_headers`. Tool calls use the OpenAI tool-format (`tools[].type=function`).

OpenRouter usage:

```yaml
model:
  provider: openai-compatible
  model: openrouter/auto:free
  api_key_env: OPENROUTER_API_KEY
  base_url: https://openrouter.ai/api/v1
  extra_headers:
    HTTP-Referer: https://github.com/...
    X-Title: my-agent
```

llama.cpp / vLLM: same shape, just point `base_url` at the local server (`http://localhost:8080/v1`).

## `anthropic`

`src/config_a2a/providers/anthropic.py`. POSTs to `/v1/messages` with `x-api-key` + `anthropic-version`. System messages are merged and sent under `system`. Tool calls round-trip as `tool_use` / `tool_result` content blocks. Default model: whatever YAML says — recommended: `claude-sonnet-4-6`, `claude-opus-4-7`.

## `google`

`src/config_a2a/providers/google.py`. `GET/POST` to `https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent?key=...`. System messages go to `systemInstruction`. Tool calls round-trip as `functionCall` / `functionResponse`. Use Gemini 3 models (`gemini-3-pro-preview`, `gemini-3-flash-preview`).

## `vertex`

`src/config_a2a/providers/vertex.py`. Same payload as Google, but hits `https://<location>-aiplatform.googleapis.com/.../models/<model>:generateContent` with an ADC bearer token. Required YAML: `project`, `location`. We prefer `google.auth.default()`; if that import fails we shell out to `gcloud auth application-default print-access-token`.

Setup:

```bash
gcloud auth application-default login
# or set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON
```

## Tool-format mapping

The MCP tool schema is converted to:

- OpenAI tool shape (`{type:"function",function:{name,description,parameters}}`),
- Anthropic tool shape (`{name,description,input_schema}`),
- Google function declarations (`{functionDeclarations:[{name,description,parameters}]}`).

Each adapter does the shaping internally; the executor passes the same `ToolSpec` list regardless of provider.
