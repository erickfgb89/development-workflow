# subagent scrolling window
There's still nothing on screen while an agent is running. we need a small scrolling context window to show us what each agent is doing.

This might be coming later in this session?

verify in a small test whether an agent streams correctly for this.

# bug: don't move on to sketch early
I asked a question during context gathering and the orchestrator moved on to the sketcher instead of continuing with the context gatherer.

We should add to the contract to recall the context gatherer until it returns a message that says it's done.