mode: quick
task: pdf-upload-modal-workspace

# PDF Upload Modal Workspace Quick Plan

## Context

- Rework the frontend PDF upload/recognition dialog in `agent_fronted/src/views/FileUpload.vue`.
- Follow the uploaded video: stable three-column workspace, independent scrolling, PDF preview on the left, Markdown editor in the middle, rendered document preview on the right.
- Keep the existing backend endpoints and API contract unchanged.

## Scope

- Inspect the current PDF upload API wrapper before editing.
- Replace any local/mock recognition path with existing real API calls if still present.
- Keep changes scoped to frontend files unless a type declaration needs to follow an existing API wrapper.
- Do not modify backend code, database code, or add endpoints.

## Verification

- Run the frontend build or the closest available static check.
- Open the upload dialog in the browser and verify the modal stays fixed, with panel-local scrolling and synced Markdown preview.
