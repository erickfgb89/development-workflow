# Overture Web UI — Mockup Specification

## Purpose

This document describes every page and modal of the Overture web dashboard, providing sufficient detail to generate high-fidelity UI mockups. It covers both current planned functionality and the data model behind each view.

---

## Application Overview

**Overture** is a local-only AI workflow orchestrator that breaks a software development goal into atomic Work Units (WUs), executes them in parallel using Claude agents, and surfaces results for human review. The web UI is additive to the existing CLI — running `overture --ui` opens the dashboard alongside the normal execution loop.

The UI is a single-page application (SPA) served from `localhost:7337`. State is pushed via WebSocket whenever `state.json` changes. Active sessions are persisted to browser `localStorage` so a page refresh does not lose context.

---

## Data Model Reference

### Session

A session lives at `.overture/sessions/{slug}/` inside the target repository.

| Field | Description |
|---|---|
| `slug` | Human-readable directory name derived from the goal (e.g., `add-user-auth`) |
| `context.md` | Markdown file with Goal, Current State, Constraints, ACs, Notes sections |
| `plan.md` | Human-readable DAG plan |
| `state.json` | Source of truth: all WU objects + `batch_number` |
| `wus/{wu_id}.json` | Per-WU spec + agent outputs |

### Work Unit (WU)

| Field | Type | Description |
|---|---|---|
| `id` | string | `WU-001`, `WU-002`, … |
| `title` | string | Short name (2–10 words) |
| `description` | string | Full implementation brief |
| `acceptance_criteria` | string[] | Ordered list of "done" conditions |
| `solution_domain` | string[] | Exhaustive list of files this WU creates/modifies |
| `dependencies` | string[] | WU IDs that must complete first |
| `status` | enum | `pending` · `in_progress` · `completed` · `failed` · `blocked` |
| `failure_count` | int | Incremented per failed review; pivot triggered at ≥ 2 |
| `needs_user_pivot` | bool | True when human intervention is required |
| `implementer_report` | object | `{ summary, files_touched, domain_breaches, commit_message }` |
| `last_review` | object | `{ verdict, summary, file_feedback[], ac_coverage[] }` |
| `error` | string\|null | Exception message if unexpected failure |

### WU Status Colors

| Status | Color |
|---|---|
| `pending` | Grey |
| `in_progress` | Blue (animated pulse) |
| `completed` | Green |
| `failed` | Red |
| `blocked` | Amber/Orange |

---

## Global Chrome

### Top Bar

- Left: **Overture** wordmark / logo
- Center: **Session tabs** — one tab per active session, labeled with the session slug. A `+` button on the right opens the New Session form. Tabs persist across refreshes via `localStorage`.
- Right: Connection indicator (WebSocket live / reconnecting)

### Left Sidebar (per session tab)

Vertical icon + label navigation. Five items:

1. **DAG** — graphical dependency view
2. **Context** — context gathering and editing
3. **Plan** — rendered plan with WU outline
4. **Agents** — chronological agent output feed
5. *(Status indicator)* — small badge showing current phase (Gathering / Planning / Executing / Done / Needs Pivot)

---

## Page 1 — Landing / New Session

*Shown on first load when no sessions are cached, and reachable from the `+` tab button at any time.*

### Layout

Full-page centered card. No sidebar.

### Form Fields

#### 1. Project Directory

- Label: **Project directory**
- Input: fuzzy-search text field
- Behavior:
  - Indexes git repositories under the user's home directory (directories containing `.git`, not recursing below them)
  - As the user types, a dropdown of matching repo paths appears
  - User may also type a path that does not match any indexed repo (accepted without error)
- Placeholder: `Search for a git repository…`

#### 2. Resume Toggle

- Label: **Resume existing session**
- Control: checkbox or toggle switch
- When checked: a **Session dropdown** appears below, populated with human-readable slug names found under `.overture/sessions/` in the selected project directory (UUID-named directories are excluded)
- The dropdown shows: slug name + last-modified timestamp

#### 3. Action Buttons (bottom of card)

- **Start** — primary button; creates a new session tab and transitions to the Context Gathering page
- **Resume** — appears only when the resume toggle is checked; loads the selected session and navigates to the appropriate page based on session phase

#### 4. Advanced / Danger Zone (collapsed by default, expandable accordion)

Used when a session is deadlocked or the user wants to redirect work. Options:

- **Reset failed WUs** — checkbox; sets all `failed` WUs back to `pending` before resuming (equivalent to `--reset-failed` CLI flag)
- **Reshape plan** — checkbox; opens a text area labeled "Reshape instructions" where the user can describe desired changes. The planner will receive the existing `context.md`, the current `plan.md`, and these instructions when re-planning.
- **Abort session** — destructive button (red, requires confirmation); marks the session abandoned

---

## Page 2 — Context Gathering

*Active immediately after a new session is started. Locked to read-only after any WU leaves `pending` status.*

### Layout

Two-column split:

- **Left (60%):** Chat conversation panel
- **Right (40%):** Collapsible `context.md` editor panel

---

### Left Panel — Agent Chat

#### Conversation area

- Scrollable message thread
- Messages styled to distinguish: **User** (right-aligned, filled bubble) vs. **Agent** (left-aligned, outline bubble)
- Agent messages render markdown

#### Input area (shown only while context gathering is active)

Three input mechanisms, all feeding the same submission:

1. **Text area** — multi-line, expands as user types; `Shift+Enter` = newline, `Enter` = submit
2. **`@` file picker** — typing `@` anywhere in the text area opens a fuzzy-search dropdown scoped to files in the target repository. Selecting a file inserts a reference token (e.g., `@src/auth.py`) that the agent receives as an attached file
3. **File upload button** — paperclip icon; opens OS file picker for uploading files from anywhere on disk into the context

#### Confirm button

- Label: **Context complete — start planning**
- Prominent, below input
- Clicking sends a `/done` signal to the Gatherer agent, which produces the final `context.md`

#### Re-engage button (post-confirm, pre-execution)

- Label: **Reopen context gathering**
- Appears only after the user confirms but before any WU has left `pending`
- Disabled (greyed out, tooltip explaining why) once any WU is `in_progress` or beyond

---

### Right Panel — `context.md` Editor

- Toggle button at top: **Show context / Hide context** (collapses/expands the panel)
- When expanded: plain markdown editor displaying the live `context.md` content
- **Save** button — writes edits back to disk
- Note at top of editor: *"Manual edits here are reflected in the next plan."* If any WU is not `pending`, show: *"Context is locked — work is in progress."* and make the editor read-only

---

## Page 3 — Plan

*Available after the Planner agent has produced `state.json`.*

### Layout

Single scrollable column with a sticky outline sidebar.

---

### Sticky Outline (left, ~20% width)

- Vertical list of WU IDs and titles
- Each item is a jump-link to the corresponding WU section
- WU ID prefixed with a status dot (color-coded per status table above)

### Main Content

#### Header

- **Plan — {session-slug}**
- **Re-plan** button (top right): opens a text input field below the header for reshape instructions. On submit, calls the Planner with existing `context.md` + `plan.md` + the user's text. While planning runs, all WU sections show a "Replanning…" overlay.

#### Per-WU Section

Each WU rendered as a card or section block:

```
WU-001 · Add user model                          [status badge]  [actions]
──────────────────────────────────────────────────────────────────────────
Description paragraph.

Acceptance Criteria:
  ✓ Login endpoint returns JWT          ← green check if completed, grey circle if not
  ✓ Password hashed with bcrypt
  ○ Token validation works

Solution Domain:
  src/models/user.py · src/routes/auth.py · tests/test_auth.py

Dependencies: WU-002, WU-003
Estimate: 25 min
```

#### WU Action Menu (per card, shown as a `…` or kebab menu)

Available actions depend on status:

| Status | Available Actions |
|---|---|
| `pending` | Reset (no-op, already pending) |
| `in_progress` | *(no actions — work in flight)* |
| `completed` | View diff (opens Diff Modal) |
| `failed` | Reset to pending · Trigger reshape |
| `blocked` | Reset to pending (also unblocks) |

**Reset to pending**: sets WU status → `pending`, clears `failure_count`. If WU was `blocked`, also clears `blocked` on this WU (upstream must be manually fixed too, or will re-block).

**Trigger reshape**: opens the Reshape / Pivot Modal (see Modals section).

---

## Page 4 — DAG

*Available after planning is complete.*

### Layout

Full-panel D3 force-directed graph. Toolbar at top.

---

### Graph

- **Nodes** — rounded rectangles, labeled with WU ID + title
- **Edges** — directed arrows from dependency → dependent (e.g., WU-002 → WU-001 means WU-001 depends on WU-002)
- **Colors** — per status table
- **Animation** — `in_progress` nodes pulse with a subtle glow
- **Zoom / pan** — standard D3 mouse interactions

### Node Click → WU Detail Popover

Clicking a node opens an inline popover (not a full modal) anchored to the node:

```
┌────────────────────────────────────────┐
│ WU-001 · Add user model     [× close]  │
│ Status: in_progress                    │
│                                        │
│ Description                            │
│ Implement the User SQLAlchemy model…   │
│                                        │
│ Solution Domain                        │
│ src/models/user.py                     │
│                                        │
│ Acceptance Criteria                    │
│ ○ Login endpoint returns JWT           │
│ ○ Password hashed with bcrypt          │
│                                        │
│ [View agent output ↗]  [View diff ↗]  │
│         (diff link only if completed)  │
└────────────────────────────────────────┘
```

- **View agent output** — scrolls to / highlights this WU's accordion in the Agents page
- **View diff** — opens the Diff Modal (only available when status = `completed`)

### Toolbar

- **Fit to screen** button — re-centers and re-scales the graph
- **Filter** dropdown — show all / show only in-progress / show only failed+blocked
- Legend strip showing color → status mapping

---

## Page 5 — Agents

*Populated as WUs complete. Empty state shown while no WUs are completed.*

### Layout

Scrollable list of WU accordions, ordered chronologically by completion time.

---

### Per-WU Accordion

Header row (always visible):

```
▶ WU-001 · Add user model   [completed]  [View diff]   12:34 PM
```

Expanded body:

#### Implementer Section

- Label: **Implementer**
- Agent summary text (freeform markdown)
- **Files changed:** list of file paths. Each file path is a link — clicking opens the Diff Modal scoped to that file.
- **Domain breaches** (if any): red callout box listing files touched outside `solution_domain`, with the agent's justification

#### Reviewer Section

- Label: **Reviewer** (and round number if > 1, e.g., "Reviewer — Round 2")
- Verdict badge: `pass` (green) · `fail` (red) · `reshape` (amber)
- Reviewer summary text
- **AC Coverage table:**

  | AC | Covered | Notes |
  |---|---|---|
  | Login endpoint returns JWT | ✓ | — |
  | Password hashed with bcrypt | ✓ | — |

- **File feedback** (if any): per-file verdict + notes

#### If multiple rounds (retry after fail)

Show Implementer + Reviewer pairs in order, separated by a subtle divider, labeled "Round 1", "Round 2".

#### Footer

- **View merged diff** link → Diff Modal for this WU's merged commit

---

## Modal A — Diff Viewer

*Triggered from: DAG node popover, Agents page, Plan page WU card.*

### Layout

Large centered modal, 80% viewport width, up to 90% height, scrollable.

### Header

```
Diff — WU-001: Add user model        [× close]
Commit: abc1234  ·  Merged at 12:34 PM
```

### File Tab Bar

Horizontal tabs, one per changed file. Badge shows `+N / -M` line counts.

### Diff Panel

- Standard unified diff view (two-column side-by-side on wide screens, single-column on narrow)
- Syntax highlighted
- Added lines: green background
- Removed lines: red background
- Line numbers shown

### Footer

- Total: `+142 / -37 lines across 4 files`

---

## Modal B — Reshape / Pivot

*Triggered from: Plan page WU action menu ("Trigger reshape"), and automatically raised when the orchestrator detects no ready WUs but work is incomplete (deadlock condition).*

### Trigger Conditions

1. **Manual** — user clicks "Trigger reshape" on a `failed` or `blocked` WU in the Plan page
2. **Automatic** — orchestrator signals deadlock (no `pending` WUs are ready but session is not complete); modal appears automatically with a banner: *"The orchestrator has no ready work units. Please choose an action."*

### Layout

Centered modal, medium size.

### Header

```
Reshape or Pivot                     [× close]
```

### Body

Short explanation of current state:
> *WU-001 (Add user model) has failed after 2 review cycles. Choose how to proceed.*

Or, for deadlock:
> *No work units are currently ready to execute. This may be caused by cascading failures. Review the options below.*

#### Options (radio or distinct action sections)

**Option 1 — Reset WU to pending**
- Resets failure count and status of the specific WU (and optionally clears `blocked` on its dependents)
- Shows a checklist of downstream WUs that will be unblocked
- Button: **Reset and continue**

**Option 2 — Reshape plan**
- Text area: *"Describe the changes you'd like the planner to make…"*
- Planner will receive: `context.md` + current `plan.md` + this text
- Button: **Reshape**
- Note: *"Reshaping will re-run the planner. WUs that are `completed` will be preserved; others may be redefined."*

**Option 3 — Abort session**
- Destructive; requires secondary confirmation (inline: "Are you sure? This cannot be undone." + **Confirm abort** button)
- Marks session abandoned; tab remains visible but greyed out

---

## Empty & Loading States

| Situation | UI |
|---|---|
| No sessions on first load | Landing / New Session page fills the viewport |
| Session tab selected, context gathering not yet started | Context Gathering page with a loading spinner while the Gatherer agent initializes |
| Planning in progress | Plan page shows a skeleton layout with a progress indicator and the text "Planner agent is working…" |
| No WUs completed yet on Agents page | Centered empty state: *"Agent output will appear here as work units complete."* |
| DAG page before planning | Centered message: *"The plan has not been generated yet."* with a link to the Context Gathering page |
| WebSocket disconnected | Top banner: *"Connection lost — retrying…"* (amber); turns green when reconnected |

---

## Interaction Notes for Mockup Generation

- **Stack**: Plain HTML + Alpine.js (reactivity) + D3.js (DAG). No build step. Fonts: system-ui or Inter.
- **Color palette**: Dark-mode preferred. Use a near-black background (`#0f1117`), slightly lighter card surfaces (`#1a1d27`), and the status colors above for WU state.
- **Sidebar**: fixed left, ~56px wide in collapsed (icon-only) state, ~200px in expanded state; toggle with a hamburger/arrow button.
- **Typography**: Monospace for file paths, diff content, WU IDs. Sans-serif for prose.
- **Responsive**: Optimize for 1280px+ desktop. Mobile is not a priority.
- **Animations**: Keep subtle — only the `in_progress` pulse and accordion expand/collapse transitions.
- **Session persistence**: Active session slugs + selected tab stored in `localStorage` key `overture_sessions`. On reload, the UI reconnects via WebSocket and restores the last active tab.
