---
mode: quick
date: 2026-05-25
task: voice-gateway-tts-state-merge
---

# Voice Gateway TTS State Merge Summary

## Changes

- Kept recognized voice text routed through the normal chat stream.
- Restored remote TTS state fallbacks so `speaking`, `tts_audio_chunk`, and `tts_playback_end` keep the voice UI state aligned in gateway playback mode.
- Re-exposed voice gateway playback helpers used by `CustomerService.vue`, including playback status, AI start/end controls, TTS playback toggling, playback-finished callback registration, and playback preparation.
- Added client-side gateway control messages for `ai_start`, `ai_end`, `start`, and `stop`.

## Verification

- `npm.cmd run build` now clears the voice-gateway related type errors.
- Build is still blocked by existing `FileUpload.vue` API signature mismatches:
  - `src/views/FileUpload.vue(927,31)`
  - `src/views/FileUpload.vue(1156,60)`
  - `src/views/FileUpload.vue(1177,37)`
  - `src/views/FileUpload.vue(1240,43)`
  - `src/views/FileUpload.vue(1261,55)`
