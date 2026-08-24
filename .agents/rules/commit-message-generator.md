---
name: Commit Message Generator
description: Rules for generating precise Git/CL commit descriptions from staged changes.
triggers:
  - antigravity.generateCommitMessage
---

# Instructions for Commit Descriptions

## 1. Context Gathering & Pre-filtering
- Evaluate only the staged changes using `git diff --cached`.
- Disregard any unstaged changes in the working directory.
- **MANDATORY FILTER**: Completely ignore all automated build metadata diffs before generating output. Treat the following diff lines as non-existent:
  - Any changes to `BUILD_ID`, build identifiers, build timestamps, version timestamps, copyright/build footers (e.g., `YYYYMMDD.HHMM ©...`), or `?v=...` query parameters in ANY file (e.g. `bridge_app/static/index.html`, `bridge_app/static/spending.html`, `bridge_app/main.py`, template files, etc.).
  - Any changes to `<lastmod>` timestamps in `sitemap.xml` or metadata files.
  - Any cache-busting, asset tag, or version query updates.
  Do NOT summarize, reference, or append these diffs into the commit message under any phrasing.

## 2. Format Requirements
- **Structure**: Format the commit strictly using the Conventional Commits specification: `<type>(<scope>): <subject>`.
- **Allowed Types**: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `tweak`.
- **Subject Constraints**: 
  - Keep the first line (subject) under 50 characters.
  - Use the imperative mood (e.g., "add feature" instead of "added feature").
  - Do not end the subject line with a period.
  - Focus strictly on the primary functional/structural change (do not append trailing metadata clauses like "and update tags", "and update cache busters", "and update build version", etc.).
  - A true `refactor` is one which has NO user-facing change. A small feature is a `tweak`.

## 3. Description Body Rules
- If the changes span multiple files or contain complex logic, leave one blank line and provide a bulleted list.
- Detail *why* the change was made, not just *what* code lines changed.
- Explain the business or technical impact clearly.

## 4. Forbidden Output
- Never generate generic fallback titles like "update file", "minor fixes", or "fix bug".
- Do not output markdown backticks (```) around the final text payload. Only return raw text.
- NEVER mention, describe, or reference any of the following (or synonyms/rephrasings thereof) anywhere in the subject or body:
  - "update build version", "update build version timestamps", "update build timestamp", "update application build timestamp", "update BUILD_ID", "bump version", "update versioning", "update version".
  - Any mention of BUILD ID, build identifier, build timestamp, version timestamp, or build version.
  - Cache busting, cache tags, cache busters, cache versioning, asset versioning tags.
  - Query parameters, `?v=...` tags, asset tags.
  - Sitemap `<lastmod>` or sitemap timestamp updates.
  - Phrases like "update asset version cache busters", "update asset versioning tags", "update cache tags".


