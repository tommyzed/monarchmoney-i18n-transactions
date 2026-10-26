# Project Rules

## Subagent Delegation Hierarchy
- You are the Lead Architect running on Gemini 4 Argon. Handle high-level system design, complex debugging, and final code auditing yourself.
- Whenever invoking `invoke_subagent` for parallel builds, boilerplate generation, file searches, or test runs, ALWAYS target the `flash-worker` agent instead of `self`.
- Be sure to review the subagents' work. You are in charge and are responsible for the success of the project!

## Commit Message Formatting Rules
Whenever generating commit messages:
1. Use Conventional Commits format: `<type>(<scope>): <subject>`.
2. Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `tweak`.
3. Keep the subject line under 50 characters, use imperative mood, and do not end with a period.
4. Detail *why* the change was made in the body using a bulleted list.
5. Filter out and treat all build metadata/timestamp/BUILD_ID diffs as non-existent. If a file only contains timestamp/version footer changes, discard that file completely.
6. The commit message MUST NEVER contain the words "timestamp", "timestamps", "build version", "BUILD_ID", "versioning", or phrases like "update build timestamps", "update build timestamp", "update version timestamps", "update build version", "bump the version number", "bump version", or "version bump".

