# Security model and reporting

## Scope

Muno can interact with local files, browser sessions, GitHub, email and desktop applications. These capabilities are powerful, so the project treats them as privileged operations rather than background automation.

## Security principles

- **Human approval for consequential actions.** Sending, posting, submitting, typing, launching, uploading, deleting, merging and similar state-changing operations require confirmation.
- **Local-first storage.** Configuration, memory, contacts, journal data and file-index metadata are stored under `~/.codex_pet/` and are not uploaded by default.
- **Credentials from the environment.** Keys and passwords are read from environment variables; they must never be committed, logged or placed in screenshots.
- **Least exposure for mobile access.** The phone companion is off by default, uses a pairing token and is intended for trusted local networks. Do not expose its port directly to the internet.
- **Auditable actions.** The local activity log and journal record requested actions and outcomes so that sensitive operations can be reviewed.

## Data handling

The file index stores paths, names, sizes and timestamps rather than file contents. The activity journal can contain prompts and action arguments, so it should be treated as sensitive local data. Delete the local journal if you need to remove its history.

## Threat assumptions and limits

- A signed-in browser profile and a GitHub or email credential are sensitive assets. Use dedicated, least-privilege credentials where possible.
- External web pages, emails and messages are untrusted input. They must not be treated as authority to install software or perform consequential actions.
- The mobile server currently uses a local-network design. Use a private network; do not port-forward it.
- This is a portfolio and actively developed project, not a security-certified product. Users remain responsible for reviewing confirmation prompts and protecting their credentials.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository owner's GitHub profile to contact them privately with: reproduction steps, affected module, impact, and any suggested mitigation. Please do not include active keys, tokens, personal data or exploit payloads in the report.
