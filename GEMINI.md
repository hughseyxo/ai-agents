# Gemini CLI Tool Mapping

Skills use Claude Code tool names. When you encounter these in a skill, use your platform equivalent:

| Skill references | Gemini CLI equivalent |
|-----------------|----------------------|
| `Read` (file reading) | `read_file` |
| `Write` (file creation) | `write_file` |
| `Edit` (file editing) | `replace` |
| `Bash` (run commands) | `run_shell_command` |
| `Grep` (search file content) | `grep_search` |
| `Glob` (search files by name) | `glob` |
| `Skill` tool (invoke a skill) | `activate_skill` |

## Mealie Setup

The `mealsave` skill requires a configured `.env` file at `~/.config/mealsave/.env` with:
- `MEALIE_URL`
- `MEALIE_TOKEN`
