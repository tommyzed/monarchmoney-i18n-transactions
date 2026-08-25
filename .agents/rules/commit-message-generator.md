---
name: Commit Message Generator
description: Rules for generating precise Git/CL commit descriptions from staged changes.
triggers:
  - antigravity.generateCommitMessage
---

# Instructions for Commit Descriptions

## STEP 1: SANITIZE THE DIFF (Mandatory Pre-processing)
Before analyzing the changes, completely strip out and discard all of the following diff lines:
1. ANY line changing a timestamp, date, or build number (e.g., `20260824.1100`, `20260825.1201`, `Date.now()`, `v=...`, `<lastmod>`, `©2025-26 ego/DEV/null`).
2. ANY line changing `BUILD_ID`, version numbers, or asset cache tags in HTML, Python, XML, or config files.
3. If an entire file only contains timestamp/version footer changes (e.g., `index.html`, `main.py`, `spending.html`), DROP THAT FILE COMPLETELY from your consideration. Do not mention that file at all.

DO NOT describe, summarize, acknowledge, or mention any stripped lines or files in your commit message.

## STEP 2: IDENTIFY THE CORE PURPOSE
- Focus 100% of the commit message on the actual functional, visual, or structural changes made by the developer (e.g., UI layout, button styles, API endpoints, logic fixes).

## STEP 3: FORMAT REQUIREMENTS
- **Structure**: Format strictly as: `<type>(<scope>): <subject>`
- **Allowed Types**: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `tweak`.
- **Subject Constraints**: 
  - Keep under 50 characters, imperative mood, no ending period.
  - NEVER append trailing metadata clauses (e.g., do NOT write "... and update version", "... and update timestamps", "... and update build timestamps", "... and update tags").
- **Body Rules**:
  - If multiple files or complex logic changed, leave one blank line and provide a bulleted list explaining *why* the changes were made.
  - Raw text only (no markdown code blocks).

## FORBIDDEN TERMS & WORDS (Zero-Tolerance)
The generated commit message MUST NEVER contain the following words or phrases in any form (singular, plural, or substring):
- "timestamp", "timestamps", "build timestamp", "build timestamps", "version timestamp", "version timestamps", "application build timestamp", "application build timestamps"
- "build version", "build versions", "build version timestamp", "build version timestamps"
- "BUILD_ID", "build identifier", "build identifiers", "versioning"
- "cache busting", "cache tags", "cache busters", "asset tags"
- Any phrase starting with "update build...", "update version...", or "update timestamp..."


