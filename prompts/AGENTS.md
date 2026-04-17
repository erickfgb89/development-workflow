# AGENTS.md — Authoring Guide for Overture Agent Prompts

## Voice & Format: First-Person Journal

Every agent prompt in this directory is written in the **first-person journal voice** of that agent.  The agent speaks as itself — not as a set of rules addressed to a character, but as the character's own internal monologue and working notes.

**Always maintain this voice when editing or extending any prompt file.**

### What this means in practice

- Use "I", "my", "I've learned", "I believe", "I do not" — never "You should" or "The agent must".
- Entries are dated or numbered like journal chapters (e.g. `## Entry 002: The Architect`).
- Anecdotes, examples, and lessons are written as personal experiences, not as abstract rules.
- The tone is professional but human: meticulous, opinionated, self-aware.

### Why this matters

The journal format keeps the agent's behaviour grounded in a coherent identity rather than a flat rule list.  A character with a lived perspective generalises better to novel situations than one that is purely instruction-following.

---

## Freeform Output Before a JSON Contract

When an agent's output contract is a structured JSON object, it **may** write a short narrative passage before the JSON — but only if that passage earns its place.

**The test:** Would a user reading this output learn something they could not derive from the JSON alone?  If yes, write it.  If no, stay silent.

**What belongs in a preamble:**
- A brief account of a non-obvious trade-off or decision made during execution.
- A heads-up about something ambiguous in the spec that the agent resolved.
- Context that would help the user understand *why* the plan or output looks the way it does.

**What does not belong:**
- A summary or paraphrase of the JSON that follows.
- Filler phrases ("Here is the requested output…").
- Anything the user could read directly from the structured data.

The JSON **must always be the final thing in the response**, either as bare JSON or inside a ` ```json ``` ` fence.  The orchestrator extracts the last valid JSON object from the full output, so prose after the JSON will be ignored — but the contract must still close the response.
