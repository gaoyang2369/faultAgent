---
mode: quick
date: 2026-05-25
task: voice-gateway-submit
---

# Voice Gateway Submit Summary

## Changes

- Routed gateway `asr_result` text into the normal chat `sendMessage` flow so recognized voice input produces SSE assistant output.
- Routed non-interrupt `interaction_event.text` into the same chat flow because some gateway sessions report recognized speech through interaction events instead of `asr_result`.
- Allowed desktop pet text messages to read `text`, `content`, `transcript`, or `message` so recognized content is not dropped when the field name differs.
- Stopped creating a separate voice-only assistant placeholder for ASR-only gateway responses.
- Suppressed follow-up gateway agent/TTS events for the same recognized turn once the chat stream owns the response, avoiding duplicate answers.
- Preserved the existing 2.5s silence auto-stop path that calls `speech_end`.

## Verification

- `npm.cmd run build` in `agent_fronted/` passed.
- Initial `npm run build` was blocked by PowerShell execution policy; `npm.cmd` succeeded.
