---
trigger: always_on
---

Rule: Insert Build Identifier
Whenever generating or updating the frontend UI, you MUST update the BUILD_ID in `index.html` before ending your turn.

Format: YYYYMMDD-HHMM

Logic: Use the current system timestamp at the time of the build/deployment (e.g. 20260404-1234).

Placement: Insert at the bottom with this template, replacing the "<BUILD_ID>" with the generated timestamp.:
        <span style="font-style: italic;">vN.N.N - <BUILD_ID> ©2026 ego/DEV/null</span>
Note that the version "vN.N.N" is dynamic. Do not mutate the version ID. REPLACE the "<BUILD_ID>"!

Scope — "frontend UI" includes ANY of the following files/constructs:
- `bridge_app/static/index.html`
- `LOADING_HTML` string inside `bridge_app/main.py` (this is HTML served to the browser)
- Any other inline HTML string in a Python/backend file that is rendered in the browser

End-of-turn checklist: Before finishing any response where you edited frontend UI, confirm:
1. Did I touch any file/string in the Scope list above? If YES →
2. Update the BUILD_ID in `bridge_app/static/index.html` to the current timestamp.