---
mode: quick
date: 2026-05-25
task: voice-gateway-tts-state-merge
---

# Voice Gateway TTS State Merge Plan

## Goal

Merge the remote voice output state optimization back into the current voice-submit flow without changing the ASR-to-chat-stream design.

## Scope

- Keep `asr_result` and recognized interaction text routed through the normal chat send pipeline.
- Add gateway TTS playback state fallbacks for `speaking`, `tts_audio_chunk`, and `tts_playback_end`.
- Avoid duplicate gateway agent/TTS handling after the chat stream owns a recognized voice turn.

## Verification

- Run the frontend build after the targeted change.
