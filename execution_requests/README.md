# Execution requests

This directory contains **reviewable execution metadata**, not credentials.

A request may be dispatched only by its logical ID, for example `exec-20260808-0001`. The workflow maps that ID to `execution_requests/<id>.json`, validates the versioned schema, prints the request digest, then stops at the GitHub Environment `remote-execution` gate before any self-hosted runner executes it.

## Required review flow

1. Commit a secret-free request document and review its exact diff.
2. Manually dispatch **Approved remote execution** with only the request ID.
3. The hosted preflight validates the request without executing it.
4. A **required reviewer** for the `remote-execution` Environment inspects the request and approves or rejects the execution job.
5. Only after approval does the `[self-hosted, remote-observer]` runner invoke `remote-observer-exec`.

All risk levels require this external approval in v1. `mode="shell"` is break-glass and must be risk `R4`.

## Public repository warning

This repository is public. Even when a request contains no secret, its executable names, arguments, logical target IDs and reason become public metadata if committed here. Do not place passwords, API keys, private-key material, bearer tokens or confidential command data in a request.

If command confidentiality becomes important, host the same schema/workflow in a private companion repository rather than weakening validation here.
