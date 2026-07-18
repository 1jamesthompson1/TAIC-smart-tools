# Vector Database Schema

The TAIC Smart Assistant uses a LanceDB vector database with two tables that share the same column schema (except `vector`). Both tables are populated by the [TAIC Report Engine](https://github.com/1jamesthompson1/TAIC-engine) pipeline.

## Tables

| Table | Rows | Content |
|---|---|---|
| `all_document_types` | ~145k | Snippets: safety issues, recommendations, report sections, summaries. Has a `vector` column for similarity search. |
| `report_text` | ~4k | Full report PDF text. No `vector` column. `document_type` is always `"report_text"`. |

## Column Reference

All columns are shared between the two tables unless noted.

| Column | Type | Description |
|---|---|---|
| `document` | `str` | The text content. For `all_document_types` this is a snippet (safety issue, recommendation, section, or summary). For `report_text` this is the full extracted PDF text. |
| `document_id` | `str` | Unique identifier for this specific document/snippet (e.g. `ATSB_a_2020_033_sum_0`). |
| `report_id` | `str` | The TAIC-engine-specific report identifier (e.g. `ATSB_a_2020_033`, `TAIC_m_2002_201`). This is the canonical key to group documents from the same report. |
| `agency_id` | `str` | The investigating agency's own identifier for the report. Format varies by agency: ATSB uses numeric (e.g. `200002648`), TAIC uses `AO-YYYY-NNN`, TSB uses `AYYPNNNN`. |
| `year` | `int` | Year of the occurrence. |
| `mode` | `str` | Transport mode: `"0"` = aviation, `"1"` = rail, `"2"` = marine. |
| `agency` | `str` | Investigating agency: `"TAIC"` (NZ), `"ATSB"` (Australia), `"TSB"` (Canada). |
| `url` | `str` \| `None` | URL to the original report on the agency's website. |
| `document_type` | `str` | Type of document. One of: `"safety_issue"`, `"recommendation"`, `"section"`, `"summary"`, or `"report_text"` (report_text table only). |
| `location` | `str` \| `None` | Standardized 4-part location string: `exact location, city/town, region/state, country`. |
| `occurrence_date` | `datetime` \| `None` | Date and time of the occurrence in ISO 8601 format (e.g. `2020-07-04 00:00:00`). |
| `occurrence_type` | `str` \| `None` | Type or classification of the occurrence. Values follow a mode-specific taxonomy (e.g. `"Engine failure or malfunction"`, `"Collision with terrain"`, `"Derailment"`). |
| `fatalities` | `int` | Number of fatalities. `0` if none. |
| `injuries` | `int` | Number of non-fatal injuries. `0` if none. |
| `publication_date` | `str` \| `None` | Date the report was published (e.g. `"2023-04-12"`). |
| `metadata_json` | `str` \| `None` | JSON string containing extracted metadata. See [Metadata JSON Structure](#metadata-json-structure) below. |
| `vector` | `float[]` | **`all_document_types` only.** Embedding vector for semantic similarity search. 1536-dimensional float array. |

For details on how the data is created — document type sourcing, extraction quality, and pipeline — see [Data Pipeline](data-pipeline.md).

## Metadata JSON Structure

The `metadata_json` column stores a JSON object with occurrence metadata and mode-specific vehicle/personnel information, all extracted from the report by the TAIC Engine LLM pipeline.

### Top-level structure

```json
{
  "occurrence": {
    "occurrence_datetime": { ... },
    "location": { ... },
    "occurrence_type": "...",
    "total_persons_involved": 4,
    "fatalities": 2,
    "injuries": 2,
    "damage_description": "...",
    "who_may_benefit": null
  },
  "aircraft": [ { ... } ],
  "trains": [ { ... } ],
  "vessels": [ { ... } ]
}
```

Only the mode-relevant vehicle key is present for each report (`aircraft` for aviation, `trains` for rail, `vessels` for marine).

### Occurrence Fields

| Field | Type | Description |
|---|---|---|
| `occurrence_datetime.local_datetime` | `str` | Local occurrence datetime (ISO 8601, e.g. `"2020-07-04T14:36"`) |
| `occurrence_datetime.time_zone` | `str` | UTC offset (e.g. `"UTC+08:00"`) |
| `occurrence_datetime.time_zone_source` | `str` \| `None` | `"explicit_in_report"` or `"inferred"` |
| `location.description` | `str` | Raw human-readable location from the report |
| `location.standardized_location` | `str` \| `None` | Normalized 4-part location |
| `occurrence_type` | `str` \| `None` | Occurrence type from mode-specific taxonomy |
| `total_persons_involved` | `int` \| `None` | Total persons involved in the occurrence |
| `fatalities` | `int` | Number of fatalities (0 if none) |
| `injuries` | `int` | Number of injuries (0 if none) |
| `damage_description` | `str` | Damage summary, or `"nil"` for no damage |
| `who_may_benefit` | `str` \| `None` | Verbatim "who may benefit" text if present |

### Aircraft Fields (`aircraft[].*`)

Each aircraft entry in the array:

| Field | Type | Description |
|---|---|---|
| `aircraft_type` | `str` \| `None` | e.g. `"Aeroplane"`, `"Helicopter"`, `"Glider"`, `"Drone/UAV/RPAS"` |
| `registration` | `str` \| `None` | Registration/tail number |
| `make` | `str` \| `None` | Manufacturer (e.g. `"Cessna"`, `"Boeing"`) |
| `model` | `str` \| `None` | Model (e.g. `"172"`, `"737-800"`) |
| `number_of_engines` | `int` \| `None` | Number of engines |
| `type_of_engines` | `str` \| `None` | e.g. `"piston"`, `"turbofan"`, `"turboprop"`, `"electric"` |
| `year_manufactured` | `int` \| `None` | Year manufactured |
| `operator` | `str` \| `None` | Operating airline/organisation |
| `flight_type` | `str` \| `None` | e.g. `"private"`, `"training"`, `"scheduled service"`, `"cargo"` |
| `persons_on_board_total` | `int` | Total persons on board |
| `persons_on_board_crew` | `int` | Crew count |
| `persons_on_board_passengers` | `int` | Passenger count |
| `damage` | `str` \| `None` | e.g. `"destroyed"`, `"substantial damage"`, `"minor damage"` |
| `pilots[].role` | `str` \| `None` | e.g. `"Captain"`, `"Sole Pilot"`, `"Instructor"` |
| `pilots[].responsibility` | `str` \| `None` | `"Pilot flying"` or `"Pilot monitoring"` |
| `pilots[].licence` | `str` \| `None` | Licence type |
| `pilots[].age` | `int` \| `None` | Pilot age |
| `pilots[].total_flying_experience` | `int` \| `None` | Total hours |
| `pilots[].experience_on_type` | `int` \| `None` | Hours on this aircraft type |

### Train Fields (`trains[].*`)

Each train entry in the array:

| Field | Type | Description |
|---|---|---|
| `train_type` | `str` \| `None` | e.g. `"passenger"`, `"freight"`, `"work train"`, `"shunt"` |
| `train_number` | `str` \| `None` | Train number or identifier |
| `length` | `float` \| `None` | Length in metres |
| `weight` | `float` \| `None` | Weight in metric tons |
| `classification` | `str` \| `None` | e.g. `"manifest"`, `"intermodal"`, `"commuter"` |
| `year_manufactured` | `int` \| `None` | Year manufactured |
| `operator` | `str` \| `None` | Railway operator |
| `operating_crew` | `int` \| `None` | Number of operating crew |

### Vessel Fields (`vessels[].*`)

Each vessel entry in the array:

| Field | Type | Description |
|---|---|---|
| `vessel_name` | `str` \| `None` | Name of the vessel |
| `vessel_type` | `str` \| `None` | e.g. `"fishing"`, `"container"`, `"tug"`, `"recreational"`, `"passenger"` |
| `classification` | `str` \| `None` | Classification society (e.g. `"Lloyd's Register"`, `"DNV"`, `"unclassed"`) |
| `length` | `float` \| `None` | Length in metres |
| `breadth` | `float` \| `None` | Beam width in metres |
| `gross_tonnage` | `float` \| `None` | Gross tonnage |
| `manufacturer` | `str` \| `None` | Shipbuilder |
| `year_built` | `int` \| `None` | Year built |
| `propulsion` | `str` \| `None` | e.g. `"diesel"`, `"diesel-electric"`, `"wind"`, `"jet"` |
| `total_power` | `float` \| `None` | Total power in kW |
| `service_speed` | `float` \| `None` | Speed in knots |
| `owner_operator` | `str` \| `None` | Owner or operator |
| `port_of_registry` | `str` \| `None` | Port or country of registry |


