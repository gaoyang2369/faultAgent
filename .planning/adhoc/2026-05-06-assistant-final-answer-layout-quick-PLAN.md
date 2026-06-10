# Assistant Final Answer Layout Quick Plan

## Context

Agent assistant messages currently render the markdown answer before process panels. Because the chat viewport auto-scrolls to the bottom during streaming and completion, users often land on task, tool, evidence, and reasoning panels instead of the final answer text.

## Scope

- Audit the frontend assistant message rendering path.
- Reorder assistant message presentation so process information appears first and final answer/report text appears last.
- Preserve SSE payloads, message data structures, tool events, task snapshots, evidence panels, report links, and history hydration.
- Verify build and available frontend utility tests.

## Non-goals

- No backend SSE protocol changes.
- No agent reasoning or workflow refactor.
- No deletion of task, tool, evidence, reasoning, or report-link UI.
