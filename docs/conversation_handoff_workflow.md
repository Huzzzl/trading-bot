# Conversation Handoff Workflow

## Why this exists

Long-running chat threads become slow, unreliable, and eventually hit
context limits. The repository must hold durable project memory. Chats
are temporary workspaces, not the source of truth.

This document defines the operating workflow for transitioning between
conversations while preserving project continuity.

---

## When to switch conversations

Switch when any condition is met:

- Every 5-7 days.
- Every 5-8 PRs.
- Earlier if the UI becomes slow.
- Earlier if context becomes unreliable.
- Before entering a major new phase.

---

## Before ending a conversation

Required steps:

1. Verify current main SHA.
2. Verify latest merged PR.
3. Verify current open PRs.
4. Verify latest full-suite test count.
5. Update `docs/project_handoff.md` with current facts.
6. Add one new session handoff under `docs/handoffs/`.
7. Record current task and next task.
8. Record safety invariants.
9. Generate a bootstrap prompt for the next conversation.

---

## At the start of a new conversation

Required steps:

1. Read `docs/project_handoff.md`.
2. Read the latest `docs/handoffs/session_*.md`.
3. Inspect current GitHub main.
4. Inspect the current main SHA.
5. Inspect the 10 most recent merged PRs.
6. Inspect the current open PR.
7. Compare handoff claims against GitHub, code, and tests.
8. Report discrepancies before continuing.
9. Use GitHub/code/tests as the source of truth.

---

## Retention model

| Layer | Purpose | Lifespan |
|---|---|---|
| Canonical handoff (`docs/project_handoff.md`) | Current facts | Updated in place |
| Session handoff (`docs/handoffs/session_*.md`) | Recent detailed context | One per conversation |
| GitHub PRs | Historical implementation record | Permanent |
| Code and tests | Actual behavior | Permanent |
| Chat | Disposable working context | One conversation |

---

## Canonical handoff update rules

- Replace stale facts; do not append every historical PR.
- Remove superseded statements.
- Keep it concise (target 300-600 lines).
- Update after major milestones or every 5-8 PRs.
- Preserve exact safety boundaries.

---

## Session handoff rules

- Create one per conversation.
- Include recent PRs, decisions, blockers, test counts, and next task.
- Session handoffs may preserve history.
- Do not treat old session handoffs as more authoritative than current
  main.

---

## Verification requirements

- Never trust a SHA, test count, PR state, or open-task claim without
  checking GitHub.
- If the handoff and GitHub disagree, state the discrepancy and follow
  GitHub.
- Never infer that paper or live trading is approved from a PASS status
  or merged PR.
