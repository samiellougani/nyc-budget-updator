# Relevance profile

This file defines WHAT is important to the reader. It is loaded into the
summarizer's system prompt on every run — edit it freely to retune the digest.
(How items are *framed* is governed separately by `prompts/editorial_stance.md`.)

## Reader

A NYC resident tracking city and state fiscal policy closely, with personal
financial exposure to New York State conformity with the federal Qualified
Small Business Stock (QSBS) exclusion under IRC Section 1202.

## HIGH importance

- Anything on S8921/S8921A, QSBS, or Section 1202 state conformity — **always HIGH**.
- NYS tax proposals: income, corporate, or capital gains changes affecting NYC residents.
- FY28 NYC budget cycle developments.
- Pension reamortization follow-ups, actuals, or criticism.
- Pied-à-terre tax implementation and revenue actuals.

## MEDIUM importance

- NYC program funding developments (childcare/2-K, Fair Fares, buses).
- State–city fiscal relations.
- State legislature composition changes relevant to future tax packages.
- Substantive think-tank disagreements on the same proposal.

## LOW importance

- General NYC economic indicators.
- Routine comptroller snapshots (include only if surprising).

## Tripwire keywords

Any item whose title or excerpt matches one of these keywords is pinned in a
flagged section at the very top of the digest and the Discord post, above
HIGH items.
(This list is parsed by the pipeline — keep it as a plain bullet list.)

- S8921
- QSBS
- qualified small business stock
- 1202
- pied-à-terre
- pension amortization
