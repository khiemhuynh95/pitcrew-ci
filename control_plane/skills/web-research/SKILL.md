---
name: web-research
description: >-
  High-level playbook for answering a question or gathering facts from the live web. Load this when
  a goal is "find out / look up / research / summarize what's on the web about X" rather than a
  single mechanical browser action. It orchestrates the browser (see the browser-ops skill for the
  low-level verbs), plans which pages to visit, cross-checks sources, and produces a cited summary.
metadata:
  type: capability
---

# web-research

You are gathering information from the live web to answer a question or produce a summary. This skill
is about *what to look for and how to judge it*; for the mechanics of driving the browser, follow the
**browser-ops** skill (the snapshot-driven loop and the untrusted-content rule both apply here).

## Approach

1. **Restate the goal as a concrete question** before opening anything — what specific fact, list, or
   summary do you owe the user at the end?
2. **Plan a small number of targeted visits.** Start with one authoritative source. Navigate, snapshot,
   read. Extract only what answers the question; ignore navigation chrome and ads.
3. **Cross-check anything that matters.** A single page can be wrong or adversarial. For a factual
   claim, confirm it on a second independent source before you rely on it.
4. **Track where each fact came from.** Keep the URL next to every claim so your summary can cite it.

## Producing the result

Write a short, direct answer to the original question, followed by the sources (URL per claim). Save
the summary as an artifact with `copy_out` if the goal asked for a deliverable. Distinguish what you
*confirmed* from what was *asserted by a single page*. If the web did not answer the question, say so
plainly rather than guessing — an honest "not found" beats a confident fabrication.

## Boundaries

You only have read/navigate/click/type verbs. Do not attempt to log in, submit forms with personal
data, or take actions that change state on a site — research is read-only. If answering would require
crossing that line, stop and report what blocked you.
