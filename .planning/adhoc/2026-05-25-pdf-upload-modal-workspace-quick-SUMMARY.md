mode: quick
task: pdf-upload-modal-workspace

# PDF Upload Modal Workspace Summary

## Changed

- Reworked `agent_fronted/src/views/FileUpload.vue` into a fixed-height modal workspace.
- Added a stable top operation bar with file status, history selection, upload/recognition, save correction, knowledge-base ingest, question draft, delete, and clear actions.
- Replaced the previous history/upload/result-column layout with three equal-height panels:
  - left: PDF source preview or upload empty state
  - middle: editable dark Markdown correction area
  - right: sanitized rendered Markdown document preview
- Kept existing `adminPdfAPI` calls and did not modify backend endpoints.
- Added request timeout handling around frontend API calls so the UI does not stay stuck indefinitely.
- Set the upload dialog to a fixed `96vh` height.
- Adjusted the dialog selector and compacted the top bar/panel headers so the window follows the taller P2-style workspace effect.
- Added Element Plus `modal-class` / `body-class` / inline dialog style overrides so the fixed height applies to the real dialog element.

## Verification

- `npm.cmd run build` passed after running outside the sandbox because Vite/esbuild child process spawning is blocked inside the sandbox.
- Vite dev server restored at `http://127.0.0.1:9005/` and returned HTTP 200.
- Browser plugin session was unavailable (`iab` and `extension` browser lists were empty), so visual browser screenshot verification could not be completed in this environment.
