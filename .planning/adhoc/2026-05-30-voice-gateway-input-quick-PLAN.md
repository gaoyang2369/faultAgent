# 2026-05-30 Voice Gateway Input Quick Plan

## Goal

Align chat voice input with the voice gateway TTS path, keep text input able to interrupt/replace an active voice turn, and remove redundant browser speech synthesis code from the chat page.

## Scope

- Frontend chat voice flow in `agent_fronted/src/views/CustomerService.vue`.
- Voice gateway session control in `agent_fronted/src/composables/useVoiceGatewaySession.ts` if needed.
- Keep existing HTTP and WebSocket contracts unchanged.

## Tasks

1. Inspect current voice recording, ASR, TTS, and text-send coupling.
2. Route voice ASR through gateway agent/TTS events instead of browser speech synthesis result-only playback.
3. Allow typed messages to interrupt active voice recording/processing/playback and send the typed content.
4. Remove redundant chat-page browser speech synthesis helpers.
5. Run focused frontend checks where practical.

