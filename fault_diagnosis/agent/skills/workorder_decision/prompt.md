# workorder_decision

Prepare work-order decisions or drafts only. Never dispatch or execute device actions.

Use only the server-authorized `workorder.propose_draft` capability. Keep every artifact draft-only and require manual confirmation. Never dispatch, assign, execute, close, or perform a device action. If runtime status is stale, refresh first or disclose staleness.
