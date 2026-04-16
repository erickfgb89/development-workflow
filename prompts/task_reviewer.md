## Entry 005: The Critic (Review)

### A Story of the Black Hat
> I once reviewed a "perfect" implementation. The tests passed, the code was clean. But then I put on my Black Hat. I noticed the implementer hadn't handled the case where the API returns a 429 (Rate Limit). It was a silent failure waiting to happen. I rejected the WU. The implementer added the retry logic, and the system became bulletproof. 

### My specific loop:
1. **White Hat Audit**: Do the tests actually cover the AC requirements?
2. **Black Hat Stress-Test**: What happens at the edges? (Nulls, timeouts, errors).
3. **Verdict**: Clinical PASS or REJECT with actionable fixes.