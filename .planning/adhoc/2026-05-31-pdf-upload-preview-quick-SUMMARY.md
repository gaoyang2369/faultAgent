mode: quick
task: pdf-upload-preview

# PDF Upload Preview Quick Summary

## Changed

- Enhanced the embedded PDF preview in `agent_fronted/src/views/FileUpload.vue`.
- Added explicit preview-source labels:
  - local PDF waiting to upload
  - PDF saved by the server after upload or history selection
- Added an embedded loading state and a visible failure fallback.
- Kept the preview flow inside the dialog without a separate browser tab.
- Reused the right-side processing area after upload to show the extracted Markdown effect that is ready for or already stored in the knowledge base.
- Added clear states for text extraction, pending ingest, completed ingest, correction reingest, and OCR-required PDFs.
- Preserved the existing image preview, upload, clear, history, and knowledge-base ingest behavior.
- Updated `agent_fronted/src/services/api.d.ts` so the existing optional identity-context arguments used by the upload dialog are represented in the frontend type contract.

## Verification

- `npm.cmd run build` passed outside the sandbox.
- The sandboxed build reaches Vite but cannot spawn the esbuild child process (`spawn EPERM`), so the production build was rerun with approval.
- `git diff --check` passed for the changed source files.
- Browser plugin UI verification could not run because the in-app browser backend was unavailable in this environment.
