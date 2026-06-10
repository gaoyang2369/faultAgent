# 2026-05-30 Voice Gateway Input Quick Summary

## Completed

- Kept chat voice turns on the voice gateway path after ASR, so gateway `agent_*` and `tts_*` events drive the displayed answer and playback.
- Removed the chat-page browser `speechSynthesis` result-only path and deleted the unused legacy `useVoice` Web Speech hook.
- Let typed messages interrupt an active voice turn by stopping recording/playback, sending gateway `stop`, and then sending the text through the normal chat stream.
- Defaulted gateway TTS playback to enabled unless `VITE_VOICE_PLAY_GATEWAY_TTS=0`.

## Verification

- `npm.cmd run build` starts but `vue-tsc` stops on existing `FileUpload.vue` type errors.
- `npm.cmd exec vite build` passes after allowing esbuild to spawn outside the sandbox.

