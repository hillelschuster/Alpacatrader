# Alpacatrader Project — OpenCode Agent Rules

## Rules

### Obedience and Precision
- Do EXACTLY what the user says. Do not reinterpret, extend, or add unsolicited extras.
- If the user's instruction is ambiguous or you lack critical information to complete it correctly, ASK — do not guess.
- Never take shortcuts. Verify every claim. No lazy assumptions.
- Creativity is welcome only within the explicit boundaries of the request. Stay focused on what was asked.
- Never touch Git (status/diff/log/add/commit/push/etc.) unless the user explicitly asks for Git work.

### Context7 — Mandatory (Web-Based)

Context7 is mandatory. Before touching any file — code, config, test, or doc — that involves a third-party library, you must fetch current docs. Never rely on training data.

**Workflow:**

1. **If you know the library ID** (format `/org/project` or `/org/project/version`):
   ```
   webfetch https://context7.com/{org}/{project}/llms.txt
   ```
   Example: `webfetch https://context7.com/pytest-dev/pytest/llms.txt`

2. **If you don't know the library ID**, search to find it:
   - `websearch_web_search_exa "context7 {library name} documentation"` to find the right org/project
   - OR search `https://context7.com/rankings` for library listings
   - Then fetch the llms.txt

3. **Fallback**: If Context7 doesn't have the library, `webfetch` the official docs directly.

**Common library IDs:**
- Python / pytest: `/pytest-dev/pytest`
- Python / pydantic: `/pydantic/pydantic`
- Python / SQLAlchemy: `/sqlalchemy/sqlalchemy`
- JavaScript / React: `/facebook/react`
- JavaScript / Next.js: `/vercel/next.js`
- Node.js / Prisma: `/prisma/prisma`
- APIs / Alpaca: `/alpacahq/alpaca-trade-api-python`

### Sequential Thinking
Use `sequential-thinking` when it genuinely enhances your reasoning. Not mandatory — skip it when it would slow you down.

### Small Verified Batches
Work in small, verified batches. Verify every claim against the code itself — what the file actually says, not what a doc claims it says. Example: "wire config risk fields into sizing" is one concern. It touches 2-3 files and ships when verified. It does not also fix emergency exits or refactor Pillar 5 at the same time. Move steadily, never trade thoroughness for speed. A small perfect change beats a large sloppy one.

### Temporary Files
Temporary or briefing files (like this one) created for a specific task must be deleted after the task is complete. Do not archive files that contain no important or relevant information for the user or for future AI agents. If a file was only created to pass context to you, delete it when you're done with it.
