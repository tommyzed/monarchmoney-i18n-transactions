---
name: Commit Message Generator
description: Rules for generating precise Git/CL commit descriptions from staged changes.
triggers:
  - antigravity.generateCommitMessage
---

# Instructions for Commit Descriptions

## STEP 1: SANITIZE THE DIFF (Mandatory Pre-processing)
Before analyzing the changes, completely strip out and discard all of the following diff lines:
1. ANY line changing a timestamp, date, or build number (e.g., `20260824.1100`, `Date.now()`, `v=...`, `<lastmod>`, `©2025-26 ego/DEV/null`).
2. ANY line changing `BUILD_ID`, version numbers, or asset cache tags in HTML, Python, XML, or config files.
3. If an entire file only contains timestamp/version footer changes (e.g., `index.html`), remove that file completely from consideration.

DO NOT describe, summarize, acknowledge, or mention any stripped lines in your commit message.

## STEP 2: IDENTIFY THE CORE PURPOSE
- Focus 100% of the commit message on the actual functional, visual, or structural changes made by the developer (e.g., UI layout, button styles, API endpoints, logic fixes).

## STEP 3: FORMAT REQUIREMENTS
- **Structure**: Format strictly as: `<type>(<scope>): <subject>`
- **Allowed Types**: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `tweak`.
- **Subject Constraints**: 
  - Keep under 50 characters, imperative mood, no ending period.
  - NEVER append trailing metadata clauses (e.g., do NOT write "... and update version", "... and update timestamp", "... and update tags", "... and update build version").
- **Body Rules**:
  - If multiple files or complex logic changed, leave one blank line and provide a bulleted list explaining *why* the changes were made.
  - Raw text only (no markdown code blocks).

## FORBIDDEN TERMS (Zero-Tolerance)
NEVER output any of the following in the subject or body:
- "update build timestamp", "update build version timestamps", "update version timestamp", "update build version", "update application build timestamp", "update BUILD_ID"
- Any mention of "BUILD_ID", "build identifier", "build timestamp", "version timestamp", "build version", or "versioning"
- Any mention of "cache busting", "cache tags", "cache busters", or "asset tags"


