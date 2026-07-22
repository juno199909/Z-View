# AGENTS.md

## Workflow

Before coding:

1. Read WORKLOG.md
2. Read PLAYBOOK.md

## Rules

- Never use git add .
- Always update WORKLOG.md after code changes.
- Never claim success without evidence.
- Include verification commands.
- Include rollback steps for production changes.
- Prefer modifying existing code over creating new abstractions.
<!-- forged-in-prod:rules -->
## Task ledger (WORKLOG.md)

Keep exactly one `WORKLOG.md` at the repo root as the single task ledger.

- **Touch code ??? append an entry.** No code touched, no entry.
- Each entry records only two things: **the goal** (what this work must deliver)
  and **where things stand** ??? including **verification evidence** (what command
  ran, what output appeared) and **the next step**.
- After a context compaction or a new session, **read the latest WORKLOG entry
  first** and continue from it. Do not re-investigate facts already verified there.
- Never create a second progress doc. One ledger, or none.

Quality bar: a fresh agent with zero context must be able to resume from the
latest entry alone. "Done" with no evidence line is not done.

## Surfacing decisions only the user can make

When you hit a blocker whose root cause is a decision only the user can make
(not something you can resolve yourself), do two things: record it where it
belongs, AND append one line to the fixed "??? Awaiting your decision" section at
the very top of `WORKLOG.md` (between the header note and the first log entry) ???
date, what's blocked, the one-sentence decision, and where the evidence is. That
section is pinned above the log stream so it never scrolls away. Strike the line
once the decision lands. Only user-decidable blockers go here; anything you can
resolve yourself does not.
<!-- /forged-in-prod:rules -->
