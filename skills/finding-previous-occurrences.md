---
name: finding-previous-occurrences
description: When the user asks to find previous occurrences which may be in relation to accident occurrences, safety issues or recommendations.
---

If not given a safety issue or recommendation you should ask for what the user wants to find previous occurrences of. If given a safety issue or recommendation you should search for previous occurrences of that issue or recommendation. 

Assume it is just for TAIC unless they ask otherwise.

To search you should do a vector search of item with minimal filters. You should use a large search (>200) and then if there are still relevant results near the end of the list you should do an even larger search (>500).

Present your findings in the following format:
A list of previous occurrences of [safety issue or recommendation] is as follows:
Title of accident with agency ID and clickable link to the accident report
A single sentence summary of the accident
A single sentence as to why this accident's safety issue or recommendation is relevant
Verbatim safety issue or recommendation from the accident report