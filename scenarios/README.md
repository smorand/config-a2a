# mcp-fs scenarios

A small, dependency-free harness that exercises the **whole mcp-fs surface**
end to end by driving the `mcp-fs-moto` config-a2a agent through realistic user
tasks over real Euro-Information documents.

## What it does

1. `client.py` — two HTTP clients (no import of the mcp-fs code):
   - `FsClient`: the mcp-fs data plane (`/api/fs`) — upload, list, mkdir, move,
     delete, download.
   - `AgentClient`: the config-a2a agent over A2A (`/agents/files/message:stream`),
     returning the parsed turn (final text, tool-call trace, state).
   Both authenticate with a locally minted RS256 user token.

2. `run_scenarios.py` — uploads a handful of real EI files into `perso-seb`
   `/inbox`, then runs six scenarios and asserts tools used + artifacts left:

   | Scenario | Exercises |
   |----------|-----------|
   | organize-inbox | `fs.tree`/`fs.list_dir`, `fs.mkdir`, `fs.move` |
   | read-pptx-summary | `fs.glob`, `fs.extract_text` (PPTX) |
   | read-pdf-question | `fs.extract_text` (PDF) |
   | docx-synthesis | `fs.extract_text` (DOCX) + `fs.write_docx` |
   | html-newsletter | `fs.read`/`fs.extract_text` (MD) + `fs.write` (HTML) |
   | image-honest-degrade | `fs.extract_text` (PNG, honest no-OCR degrade) |

## Run

The stack must be up (moto :5001, mcp-fs :5002, config-a2a :5003). Then:

```bash
uv run python -m scenarios.run_scenarios
```

Exit code is 0 only when all scenarios pass. Override the target with env vars
`MCP_FS_URL`, `AGENT_URL`, `MCP_FS_KEY`, `MCP_FS_EMAIL`, `MCP_FS_MOUNT`.
