---
name: financial-evidence-research
description: Collect, retrieve, and cross-check financial evidence for company research. Use when a research question needs current news, exchange announcements, financial statements, business structure, market data, or source verification before analysis.
---

# Financial Evidence Research

Build conclusions from traceable evidence. Treat search results as candidates, not facts.

## Workflow

1. Resolve the target entity and ticker before searching.
2. Inspect existing evidence coverage and select one missing capability.
3. Prefer local indexed evidence, then use public web search for uncovered facts.
4. Fetch article or filing content; discard search landing pages and off-target issuers.
5. Deduplicate by canonical URL and evidence identity.
6. Preserve source, publication time, as-of date, and content excerpt.
7. Require primary or independent corroborating evidence for material claims.
8. Stop when required capabilities are covered or available tools are exhausted.

## Guardrails

- Use exchange filings and company disclosures before media commentary.
- Keep financial periods, units, currencies, and year-over-year bases explicit.
- Never infer a customer or supplier relationship from industry-chain proximity.
- Never treat remembered evidence as current without checking its as-of date.
- Record missing evidence instead of inventing an estimate.

Read [references/evidence-policy.md](references/evidence-policy.md) when assigning source authority or deciding whether a claim is sufficiently verified.
