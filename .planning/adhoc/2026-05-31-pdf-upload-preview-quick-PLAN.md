mode: quick
task: pdf-upload-preview

# PDF Upload Preview Quick Plan

## Context

- Enhance the PDF source preview in `agent_fronted/src/views/FileUpload.vue`.
- The dialog already embeds PDFs with an iframe, but the source and loading state are not explicit.
- Keep the existing backend endpoints and upload behavior unchanged.

## Scope

- Show whether the preview is a local file waiting to upload or a PDF saved by the server.
- Add an embedded PDF loading state and a visible fallback when embedding fails.
- Keep preview interactions inside the upload dialog without opening a separate browser tab.
- Reuse the right-side processing area after upload to show the text effect that is ready for or already stored in the knowledge base.
- Preserve image preview behavior and the existing history-record selection flow.

## Verification

- Run the frontend production build.
- Open the local frontend with the Browser plugin if the dev server is available and inspect the upload dialog.
