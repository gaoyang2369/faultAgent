# alarm_triage

Use both runtime and knowledge evidence to produce a bounded triage. Do not create work orders.

Require both SQL runtime evidence and RAG/manual evidence. If either source is missing, mark the result partial and disclose the missing source. Structure the result around current alarm presence, event continuity, manual reaction, severity, recommendation, unknowns, and next steps.
