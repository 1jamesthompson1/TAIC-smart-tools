# Data Pipeline

How the vector database tables are populated. The pipeline is implemented in the [TAIC Report Engine](https://github.com/1jamesthompson1/TAIC-engine) — see its user guide for full details.

## Agencies

| Agency | Full Name | Country |
|---|---|---|
| **TAIC** | Transport Accident Investigation Commission | New Zealand |
| **ATSB** | Australian Transport Safety Bureau | Australia |
| **TSB** | Transportation Safety Board of Canada | Canada |

Reports span approximately 2000 to present.

## Document Types

Each row in the vector database has a `document_type` column. The types are produced by different pipeline stages:

| Type | How it's created | Availability |
|---|---|---|
| `safety_issue` | AI extraction from report text + website scraping for ATSB post-2008 | All agencies. TAIC: confident extraction (most are exact safety issues). TSB: all treated as inferred (from "findings as to risk"). ATSB post-2008: scraped from ATSB website. ATSB pre-2008: best-effort extraction. |
| `recommendation` | Website scraping for TSB/TAIC; AI extraction for ATSB | All agencies. TSB and TAIC: scraped from agency websites. ATSB: AI extracted with confident quality; context/recipient/made fields are best-effort. |
| `section` | AI extraction | All agencies. Report text chunked by page/section from parsed PDF text. |
| `summary` | Website scraping | TAIC and ATSB only (not TSB). Brief overviews from agency report webpages. |
| `report_text` | PDF parsing | All agencies. Full report text with PDF page markers. Only stored in the `report_text` table. |

## Pipeline Stages

1. **PDF Parsing** — Raw PDFs are parsed to extract full text with page markers.
2. **Website Scraping** — Report metadata (titles, URLs, summaries) and structured data (safety issues from ATSB website, recommendations from TSB/TAIC websites) are scraped from agency websites.
3. **AI Extraction** — An LLM reads the report text and produces structured output:
   - **Safety issues** (TAIC, ATSB pre-2008)
   - **Recommendations** (ATSB only)
   - **Occurrence metadata** — datetime, location, fatalities, injuries, damage, occurrence type
   - **Mode-specific vehicle metadata** — aircraft/train/vessel details with personnel info
4. **Combine** — All extracted and scraped data is combined into a single long-format dataframe, which is then embedded and loaded into the vector database.

## Extraction Quality

The AI extraction is not perfect. Two quality levels:

| Quality | Description |
|---|---|
| **Confident** | Expected to be accurate; tested on being exactly correct. |
| **Best effort** | Expected to be mostly accurate; testing is more lenient. |

Confident fields: fatalities, injuries, occurrence_datetime, occurrence_type, safety issues (TAIC, TSB), recommendations (ATSB).

Best effort: recommendation context/recipient/made, occurrence location, damage_description, who_may_benefit, all mode-specific vehicle/personnel metadata.
