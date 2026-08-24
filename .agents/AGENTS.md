# Project Rules

## Do Not Run Tests Automatically
Do NOT run tests or execute test commands (e.g. `pytest`, virtualenv pytest, etc.) unless the user specifically and explicitly instructs you to do so in the request.

## Commit Message Formatting Rules
Whenever generating commit messages:
1. Use Conventional Commits format: `<type>(<scope>): <subject>`.
2. Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`.
3. Keep the subject line under 50 characters, use imperative mood, and do not end with a period.
4. Detail *why* the change was made in the body using a bulleted list.
5. NEVER mention that the BUILD ID, versioning, or application build timestamp was updated/incremented.
6. Strictly OMIT phrases like "update build timestamp", "update build version timestamps", "update application build timestamp", "update BUILD_ID", or similar timestamp/versioning references from both the commit subject line and the body.

