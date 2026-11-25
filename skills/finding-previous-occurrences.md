---
name: finding-previous-occurrences
description: When the user asks to find previous occurrences of a specific safety issue, recommendation, or accident occurrence. Only use when the user supplies a specific issue or recommendation. Do not use for aggregate counts, trends, or broad topic searches.
---

This skill is for finding previous occurrences of a specific safety issue, recommendation, or accident occurrence. Do not use it for aggregate counts, totals, trends, or broad topic sweeps; use the `analyze_results` or `search` tools for those.

If not given a specific safety issue, recommendation, or accident occurrence, you should ask what the user wants to find previous occurrences of. If given one, search for previous occurrences of it. 

Assume it is just for TAIC unless they ask otherwise.

To search you should do a vector search of item with minimal filters. You should use a large search (>200) and then if there are still relevant results near the end of the list you should do an even larger search (>500).

Present your findings in the following format:
A list of previous occurrences of [safety issue or recommendation] is as follows:
Title of accident with agency ID and clickable link to the accident report
A single sentence summary of the accident
A single sentence as to why this accident's safety issue or recommendation is relevant
Verbatim safety issue or recommendation from the accident report