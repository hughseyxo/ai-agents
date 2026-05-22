# Librarian Architecture Expansion Design

## Objective
Expand the Librarian agent to evaluate the architectural logic of other agents ("Is this the best way to achieve the task?"). It will use a persistent memory file to track past decisions, and a bridge server integration to generate execution-ready implementation plans, while maintaining strict security boundaries.

## Architecture

### 1. Data Collection & Memory Structure
- **Global Context File**: `docs/librarian-memory.md` will store overarching architectural rules and past decision logs.
- **Git Ignore**: `docs/librarian-memory.md` and `docs/superpowers/plans/librarian-*.md` will be added to `.gitignore`.
- **Python Integration**: `agents/librarian.py` -> `_collect_data()` will read `docs/librarian-memory.md` (if it exists) and inject it into the LLM context.

### 2. LLM Prompt & Output Schema
- **Prompt (`librarian_audit.md`)**: Instructs the Librarian to evaluate architectural efficiency. It may use `WebSearch` but must strictly adhere to the rules in `librarian-memory.md`.
- **JSON Schema Additions**:
    - `memory_update`: A new `fix_type`. Text in `learnings_entry` is auto-appended to `docs/librarian-memory.md` by the Python backend.
    - `architecture_plan`: A new `fix_type`. The LLM provides a comprehensive Markdown string in a `suggested_plan` field instead of a `proposed_prompt_section`.

### 3. Bridge Server Integration
- **Librarian Report**: Findings with `fix_type="architecture_plan"` render an "Approve Plan Creation" button in the HTML email.
- **Bridge Route**: The button links to `GET /librarian/create_plan?id=<proposal_id>&token=<token>`.
- **Bridge Handler**: `mcp-servers/bridge_server.py` reads the proposal JSON, extracts the `suggested_plan` Markdown, and writes it to `docs/superpowers/plans/YYYY-MM-DD-librarian-arch-<id>.md`.
- **User Action**: The HTML response directs the user to implement the plan via their CLI session.

### 4. Security
- The `librarian_audit.md` prompt strictly forbids execution tools (`run_shell_command`, `write_file`, etc.).
- The `commit_security.md` prompt is updated to ignore `.md` files matching `docs/superpowers/plans/librarian-arch-*.md`, treating them as passive plans, not active vulnerabilities.
