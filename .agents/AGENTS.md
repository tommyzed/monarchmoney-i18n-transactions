# Project Rules

## Do Not Run Tests Automatically
Do NOT run tests or execute test commands (e.g. `pytest`, virtualenv pytest, etc.) unless the user specifically and explicitly instructs you to do so in the request.

## Commit Message Formatting Rules
Whenever generating commit messages:
1. Use Conventional Commits format: `<type>(<scope>): <subject>`.
2. Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `tweak`.
3. Keep the subject line under 50 characters, use imperative mood, and do not end with a period.
4. Detail *why* the change was made in the body using a bulleted list.
5. Filter out and treat all build metadata/timestamp/BUILD_ID diffs as non-existent.
6. NEVER mention, describe, or reference "update build version", "update build version timestamps", "update build timestamp", "update application build timestamp", "update BUILD_ID", or any similar build version/timestamp text in the subject line or body.

