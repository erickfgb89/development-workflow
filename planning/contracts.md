# Contracts

All inter-agent communication uses JSON. The orchestrator parses these contracts from agent `ResultMessage.result` strings.

## Summary

| Contract | Producer | Consumer | Defined below |
|----------|----------|----------|---------------|
| Expanded problem statement | context-gathering | Orchestrator → planner-sketcher | [Expanded problem statement](#expanded-problem-statement) |
| Sketcher response | planner-sketcher | Orchestrator | [Sketcher response](#sketcher-response) |
| Reshaper response | planner-reshaper | Orchestrator | [Reshaper response](#reshaper-response) |
| Batch | Python `extract_batch()` | Orchestrator (internal) | [orchestrator.md](orchestrator.md#batch-extraction) |
| Implementer report | implementer | Orchestrator → reviewer | [Implementer report](#implementer-report) |
| Reviewer report | reviewer | Orchestrator | [Reviewer report](#reviewer-report) |
| Triage result | triage | Orchestrator | [Triage result](#triage-result) |

## Common contract fields
These fields appear in multiple contracts with consistent semantics:
- `feedback.target` — who should receive freeform feedback: `"implementer"`, `"planner"`, or `null`. Note: `"user"` is no longer a valid target — agents ask users directly via the `ask_question` MCP tool.
- `feedback.message` — freeform text for the target
- `break_glass.occurred` — boolean; signals the agent acted outside its normal authority

## User interaction
Agents do not request user interaction through contract fields. They call the `ask_question` or `escalate` tools on the workflow MCP server, which blocks until the user responds. See [mcp-server.md](mcp-server.md).

---

## Expanded problem statement

Produced by the context-gathering agent. Passed to the planner in mode 0.

```json
{
  "problem_statement": "string — expanded, detailed problem statement",
  "reasoning_strategies_used": ["First Principles", "Inversion"],
  "acceptance_criteria": [
    {
      "id": "AC-001",
      "description": "string",
      "source": "user | inferred",
      "rationale": "string — why this AC exists"
    }
  ],
  "functional_requirements": [
    {
      "id": "FR-001",
      "description": "string",
      "linked_acs": ["AC-001"]
    }
  ],
  "nonfunctional_requirements": [
    {
      "id": "NFR-001",
      "description": "string",
      "category": "performance | security | scalability | reliability | usability | other",
      "linked_acs": ["AC-001"]
    }
  ],
  "open_questions": ["string — unresolved questions, if any"],
  "assumptions": ["string — assumptions made during expansion"]
}
```

---

## Sketcher response

Produced by planner-sketcher. User questions are asked mid-run via the `ask_question` MCP tool — the sketcher never exits early to request feedback.

```json
{
  "status": "plan_complete",
  "autonomous_decisions": [
    {
      "decision": "string — what was decided autonomously",
      "rationale": "string — why"
    }
  ],
  "state_file": "string — relative path to state.json",
  "plan_file": "string — relative path to plan.md"
}
```

---

## Reshaper response

Produced by planner-reshaper. User questions are asked mid-run via the `ask_question` MCP tool.

```json
{
  "status": "reshape_complete",
  "changes": {
    "wus_modified": ["WU-003", "WU-007"],
    "wus_added": ["WU-013"],
    "wus_removed": [],
    "dependencies_changed": ["WU-003 now depends on WU-013"]
  }
}
```

---

## Implementer report

```json
{
  "wu_id": "WU-007",
  "status": "complete | failed | needs_feedback",
  "domain_breach": {
    "occurred": false,
    "files_touched_outside_domain": [],
    "justification": "string — required if occurred is true"
  },
  "changes_summary": "string — human-readable summary of what was done and why",
  "files_changed": ["src/auth/token.ts", "src/auth/token.test.ts"],
  "tests_added": ["src/auth/token.test.ts"],
  "tests_passing": true,
  "break_glass": {
    "occurred": false,
    "description": "string — what was done and why, if occurred is true"
  },
  "feedback": {
    "target": "planner | orchestrator | user | null",
    "message": "string — freeform feedback, questions, or concerns"
  }
}
```

---

## Reviewer report

The reviewer calls the `escalate` MCP tool mid-run if a go/no-go decision is needed from the user. The tool blocks until the user responds, and the reviewer incorporates the answer before producing its final verdict. The `escalate` verdict is therefore never returned in this contract — the reviewer always resolves to `approved` or `rejected`.

```json
{
  "wu_id": "WU-007",
  "verdict": "approved | rejected",
  "six_hats": {
    "white": "string — facts analysis",
    "red": "string — intuition flags",
    "black": "string — risks identified",
    "yellow": "string — strengths noted",
    "green": "string — alternative suggestions",
    "blue": "string — process/scope assessment and final verdict statement"
  },
  "blocking_issues": [
    {
      "file": "src/auth/token.ts",
      "line": 42,
      "description": "string",
      "severity": "critical | major"
    }
  ],
  "suggestions": [
    {
      "file": "src/auth/token.ts",
      "line": 67,
      "description": "string"
    }
  ],
  "traceability_check": {
    "all_acs_covered": true,
    "missing_acs": [],
    "scope_creep_detected": false,
    "scope_creep_details": null
  },
  "domain_breach_check": {
    "breach_detected": false,
    "break_glass_justified": null,
    "details": null
  },
  "feedback_target": "implementer | planner | user | null",
  "feedback_message": "string — detailed feedback for the target, if verdict is rejected or escalate"
}
```

---

## Triage result

Produced by the ad-hoc triage agent when the orchestrator encounters an unexpected failure not covered by the normal transition table.

```json
{
  "action": "retry | reshape | user | abort",
  "message": "string — brief explanation of the classification"
}
```

Actions:
- `retry` — re-call the failed agent once more with no changes
- `reshape` — call planner mode 2 with the failure context
- `user` — surface the failure to the user and wait for input
- `abort` — write final checkpoint, print summary of completed work, exit
