---
name: Commit Message Generator
description: Rules for generating precise Git/CL commit descriptions from staged changes.
triggers:
  - antigravity.generateCommitMessage
---

# Instructions for Commit Descriptions

## 1. Context Gathering
- Evaluate only the staged changes using `git diff --cached`.
- Disregard any unstaged changes in the working directory.

## 2. Format Requirements
- **Structure**: Format the commit strictly using the Conventional Commits specification: `<type>(<scope>): <subject>`.
- **Allowed Types**: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`.
- **Subject Constraints**: 
  - Keep the first line (subject) under 50 characters.
  - Use the imperative mood (e.g., "add feature" instead of "added feature").
  - Do not end the subject line with a period.

## 3. Description Body Rules
- If the changes span multiple files or contain complex logic, leave one blank line and provide a bulleted list.
- Detail *why* the change was made, not just *what* code lines changed.
- Explain the business or technical impact clearly.

## 4. Forbidden Output
- Never generate generic fallback titles like "update file", "minor fixes", or "fix bug".
- Do not output markdown backticks (```) around the final text payload. Only return raw text.
- Never mention that the BUILD ID or versioning was incremented.

