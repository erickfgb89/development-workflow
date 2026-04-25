# UI Wiring Debt

Audit of interactive elements that are either dead, partially wired, or use placeholder data.
Status updated as items are fixed.

---

## Fixed this session

| # | Location | Element | Fix |
|---|----------|---------|-----|
| 1 | context page | `@` trigger / file picker | Wired to real `/api/files` endpoint |
| 2 | context page | `📎 attach` button | Wired to hidden `<input type="file">` |
| 3 | plan page | "↓ expand all" label | Fixed to toggle label (↓ / ↑) based on current state |
| 4 | landing | "Start new instead" handler | Simplified from broken ternary to direct `startNewSession()` |
| 5 | plan/agents/dag | Diff modal placeholder content | Wired to real `/api/diff` endpoint; `merge_branch()` now stores `commit_sha` in state |

---

## Remaining

### MEDIUM — label/state mismatch (cosmetic but confusing)

**[B] "view agent output ↗" in DAG popover navigates away but doesn't filter to that WU**
- File: `index.html` ~line 483
- Handler: `@click="navigate('agents')"`
- The agents page shows all WUs in accordion form. Clicking this button in the popover
  for a specific WU takes you to agents page but doesn't scroll/expand the right entry.
- Fix: pass the WU id and auto-expand + scroll-to that accordion on navigation.
  e.g. `navigate('agents', wu.id)` → in `navigate()`, if page=agents and a wuId is
  provided, set `agentAccOpen[wuId] = true` then `$nextTick` scroll to it.

### LOW — cosmetic

**[C] DAG "view agent output ↗" button in popover navigates to agents page but closes popover
  without feedback — acceptable but slightly jarring. Low priority.**

**[D] `abortSession()` on the landing page danger zone**
- File: `index.html` ~line 174
- The method works (shows confirm dialog, calls `/api/session/abort`), but the button
  is inside the landing card where no session is selected yet. If `activeSession` is
  null it will fail silently.
- Fix: disable the button when `!activeSession`, or hide the danger zone when no
  session is active.

---

## Out of scope (intentional stubs)

- Diff line-level syntax highlighting — not wired, not promised in design.
- Agents page "stream live output" — real-time log streaming is post-MVP.
