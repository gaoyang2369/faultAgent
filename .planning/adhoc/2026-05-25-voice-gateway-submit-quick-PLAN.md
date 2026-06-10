---
mode: quick
date: 2026-05-25
task: voice-gateway-submit
---

# Voice Gateway Submit Plan

## Goal

Fix the voice input flow so gateway-recognized speech is submitted through the normal chat/SSE path and produces assistant output.

## Scope

- Keep the existing voice gateway ASR flow.
- Preserve the 2.5s silence auto-stop behavior.
- Route final recognized text into the existing chat send pipeline.
- Avoid duplicate user messages when the gateway only returns ASR text.

## Verification

- Run frontend type/build checks where available.
- Inspect the changed flow for duplicate submission and blocked streaming states.
