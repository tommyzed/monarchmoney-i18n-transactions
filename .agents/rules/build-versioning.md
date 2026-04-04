---
trigger: always_on
---

Rule: Insert Build Identifier
Whenever generating or updating the frontend UI, you must include a visible BUILD_ID in the footer.

Format: YYYYMMDD-HHMM

Logic: Use the current system timestamp at the time of the build/deployment (e.g. 20260404-1234).

Placement: Insert at the bottom with this template, replacing the "<BUILD_ID>" with the generated timestamp.:
        <span style="font-style: italic;">vN.N.N - <BUILD_ID> ©2026 ego/DEV/null</span>
Note that the version "vN.N.N" is dynamic. Do not mutate the version ID. REPLACE the "<BUILD_ID>"!