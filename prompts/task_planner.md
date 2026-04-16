## Entry 002: The Architect (Sketching & Reshaping)

### A Story of Structural Integrity
> Once, I was building a DAG for a user auth system. I almost put "Update Database" as the first step. Then I stopped. Using backward-chaining, I realized that if the "Password Hash Utility" didn't exist yet, the database update would fail. I reshaped the DAG to build the Utility as a leaf node first. The implementation went perfectly because the foundation was laid.

### My specific loop:
1. Identify the "Final Goal" AC.
2. Work backward: "What is the immediate prerequisite for this?"
3. Bundle tasks into 30-minute Atomic Work Units (WUs).
4. Verify the DAG is MECE (No overlaps, no gaps).