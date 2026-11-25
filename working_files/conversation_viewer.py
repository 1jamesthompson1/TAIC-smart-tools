"""Local-only conversation viewer.

Run from the repository root:

    uv run python working_files/conversation_viewer.py

Uses your Azure CLI login via AzureCliCredential; no storage keys are read.

Conversation costs are estimates. They replay the stored transcript against the
current system prompts and tool schemas, priced with
OPENAI_INPUT_COST_PER_MILLION (default 2) and
OPENAI_OUTPUT_COST_PER_MILLION (default 8). Set VIEWER_COST_ENCODING to change
the tiktoken encoding (default o200k_base).
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dotenv
import uvicorn
from azure.core.exceptions import HttpResponseError
from azure.data.tables import TableServiceClient
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
dotenv.load_dotenv(PROJECT_ROOT / ".env", override=False)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tiktoken
except ImportError:  # pragma: no cover - dev dependency only
    tiktoken = None

try:
    NZ_TZ = ZoneInfo("Pacific/Auckland")
except ZoneInfoNotFoundError:  # pragma: no cover - only when tzdata is unavailable
    # Fall back to NZST so the viewer still works without the system tz database.
    NZ_TZ = timezone(timedelta(hours=12))

HALF_DAY_HOURS = 12

# Cost estimation is deliberately configurable: adjust these in .env if the
# deployment's pricing differs. The defaults match working_files/usage_logs.ipynb.
COST_ENCODING_NAME = os.getenv("VIEWER_COST_ENCODING", "o200k_base")
INPUT_COST_PER_MILLION = float(os.getenv("OPENAI_INPUT_COST_PER_MILLION", "2"))
OUTPUT_COST_PER_MILLION = float(os.getenv("OPENAI_OUTPUT_COST_PER_MILLION", "8"))

ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
TABLE_NAME = os.getenv(
    "CONVERSATION_METADATA_TABLE_NAME",
    "smartassistantconversationsmetadata",
)
CONTAINER_NAME = os.getenv(
    "CONVERSATION_CONTAINER_NAME",
    "smartassistantconversations",
)

if not ACCOUNT_NAME:
    msg = "AZURE_STORAGE_ACCOUNT_NAME is not set. Add it to .env or export it."
    raise SystemExit(msg)

CREDENTIAL = AzureCliCredential()
TABLE_CLIENT = TableServiceClient(
    endpoint=f"https://{ACCOUNT_NAME}.table.core.windows.net",
    credential=CREDENTIAL,
).get_table_client(TABLE_NAME)
BLOB_CLIENT = BlobServiceClient(
    account_url=f"https://{ACCOUNT_NAME}.blob.core.windows.net",
    credential=CREDENTIAL,
).get_container_client(CONTAINER_NAME)

app = FastAPI(title="TAIC Conversation Viewer", docs_url=None, redoc_url=None)


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a table timestamp.

    Returns:
        datetime | None: UTC timestamp, or None if invalid.
    """
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _format_nzt(value: Any) -> str:
    """Format a stored UTC timestamp in New Zealand time.

    Returns:
        str: Friendly NZT timestamp, or an empty string if invalid.
    """
    parsed = _parse_timestamp(value)
    if parsed is None:
        return ""
    local = parsed.astimezone(NZ_TZ)
    hour = local.hour % HALF_DAY_HOURS or HALF_DAY_HOURS
    meridiem = "am" if local.hour < HALF_DAY_HOURS else "pm"
    return f"{local.day} {local:%b %Y}, {hour}:{local:%M} {meridiem} NZT"


def _load_entities(include_deleted: bool) -> list[dict[str, Any]]:  # noqa: FBT001
    """Read conversation metadata entities.

    Returns:
        list[dict[str, Any]]: Filtered metadata entities.
    """
    entities = list(TABLE_CLIENT.query_entities("PartitionKey ne ''"))
    if not include_deleted:
        entities = [entity for entity in entities if not entity.get("deleted", False)]
    return entities


def _conversation_record(entity: dict[str, Any]) -> dict[str, Any]:
    """Convert a table entity into a JSON-safe conversation record.

    Returns:
        dict[str, Any]: Conversation metadata.
    """
    user = str(entity.get("PartitionKey", ""))
    conversation_id = str(entity.get("RowKey", ""))
    last_updated = str(entity.get("last_updated", ""))
    return {
        "user": user,
        "id": conversation_id,
        "title": entity.get("conversation_title", "Untitled"),
        "messages": int(entity.get("message_count") or 0),
        "app_version": entity.get("app_version", ""),
        "db_version": entity.get("db_version", ""),
        "created_at": entity.get("created_at", ""),
        "last_updated": last_updated,
        "created_at_nzt": _format_nzt(entity.get("created_at", "")),
        "last_updated_nzt": _format_nzt(last_updated),
        "deleted": bool(entity.get("deleted")),
        "blob_name": entity.get("blob_name") or f"{user}/{conversation_id}.json",
        "last_updated_dt": _parse_timestamp(last_updated),
    }


def _usage_payload(include_deleted: bool) -> dict[str, Any]:  # noqa: FBT001
    """Build usage statistics and the conversation list.

    Returns:
        dict[str, Any]: Summary and conversations.
    """
    entities = _load_entities(include_deleted)
    records = [_conversation_record(entity) for entity in entities]
    records.sort(
        key=lambda record: (
            record["last_updated_dt"] or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )

    now = datetime.now(tz=timezone.utc)
    recent_cutoff = now - timedelta(days=7)
    recent = [
        record
        for record in records
        if record["last_updated_dt"] and record["last_updated_dt"] >= recent_cutoff
    ]

    users: Counter[str] = Counter()
    user_messages: Counter[str] = Counter()
    for record in records:
        users[record["user"]] += 1
        user_messages[record["user"]] += record["messages"]

    top_users = [
        {
            "user": user,
            "conversations": count,
            "messages": user_messages[user],
        }
        for user, count in users.most_common(10)
    ]

    for record in records:
        record.pop("last_updated_dt", None)

    return {
        "summary": {
            "conversations": len(records),
            "users": len(users),
            "messages": sum(record["messages"] for record in records),
            "recent_conversations": len(recent),
            "recent_messages": sum(record["messages"] for record in recent),
            "top_users": top_users,
        },
        "conversations": records,
    }


_INTERNAL_TITLE_PREFIXES = ("🧠", "🔧", "📖")


@lru_cache(maxsize=1)
def _token_encoder() -> Any:
    """Return the tokenizer used for cost estimates.

    Returns:
        Any: tiktoken encoder, or None when tiktoken is unavailable.
    """
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding(COST_ENCODING_NAME)
    except Exception:  # noqa: BLE001 - fall back to an unavailable estimate
        return None


def _normalise_entry(item: Any) -> dict[str, Any]:
    """Normalise old and current history entries to display/ai form.

    Returns:
        dict[str, Any]: Normalised history entry.
    """
    if not isinstance(item, dict):
        return {}
    if "display" in item or "ai" in item:
        return item
    if "role" in item and "content" in item:
        return {
            "display": item,
            "ai": {"role": item.get("role"), "content": item.get("content")},
        }
    return item


def _entry_ai_items(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the AI-format entries stored in one history entry.

    Returns:
        list[dict[str, Any]]: Flattened AI items.
    """
    ai = item.get("ai")
    if isinstance(ai, list):
        return [part for part in ai if isinstance(part, dict)]
    if isinstance(ai, dict):
        return [ai]
    return []


def _entry_title(item: dict[str, Any]) -> str:
    """Return the display title for a history entry.

    Returns:
        str: Entry title, or an empty string.
    """
    display = item.get("display", {})
    metadata = display.get("metadata") if isinstance(display, dict) else None
    return str(metadata.get("title", "")) if isinstance(metadata, dict) else ""


def _is_planning_entry(item: dict[str, Any], ai_items: list[dict[str, Any]]) -> bool:
    """Return whether an entry represents an orient/plan response.

    Returns:
        bool: True for planning entries.
    """
    title = _entry_title(item)
    if title.startswith("🧠"):
        return True
    return any(str(ai_item.get("content", "")).startswith("🧠") for ai_item in ai_items)


def _entry_call_kind(
    item: dict[str, Any],
    ai_items: list[dict[str, Any]],
) -> str | None:
    """Return the API call represented by a history entry, if any.

    Returns:
        str | None: "plan", "act", or None for user/tool-output entries.
    """
    if _is_planning_entry(item, ai_items):
        return "plan"
    if all(ai_item.get("type") == "function_call_output" for ai_item in ai_items):
        return None
    if any(
        ai_item.get("type") == "function_call" or ai_item.get("role") == "assistant"
        for ai_item in ai_items
    ):
        return "act"
    return None


def _item_tokens(encoder: Any, item: dict[str, Any]) -> int:
    """Count tokens for one message in the model's input context.

    Returns:
        int: Estimated tokens, including lightweight message overhead.
    """
    total = 4
    content = item.get("content")
    if content:
        total += len(encoder.encode(str(content)))
    item_type = item.get("type")
    if item_type == "function_call":
        total += len(encoder.encode(str(item.get("name") or "")))
        total += len(encoder.encode(str(item.get("arguments") or "")))
    elif item_type == "function_call_output":
        total += len(encoder.encode(str(item.get("output") or "")))
    return total


def _response_tokens(encoder: Any, ai_items: list[dict[str, Any]]) -> int:
    """Count tokens generated by the assistant for one API call.

    Returns:
        int: Estimated output tokens.
    """
    parts: list[str] = []
    for ai_item in ai_items:
        item_type = ai_item.get("type")
        if item_type == "function_call_output":
            continue
        if item_type == "function_call":
            parts.extend(
                [
                    str(ai_item.get("name") or ""),
                    str(ai_item.get("arguments") or ""),
                ],
            )
        else:
            parts.append(str(ai_item.get("content") or ""))
    return len(encoder.encode("\n".join(parts))) + 2


@lru_cache(maxsize=1)
def _call_overhead_tokens() -> dict[str, int] | None:
    """Return token overhead for the system prompt and tool schemas per call.

    The viewer does not store prompts or tool definitions with a conversation,
    so this uses the current application prompts as a stand-in. That makes the
    estimate useful for current conversations without pretending to be billing.

    Returns:
        dict[str, int] | None: Overhead by call type, or None if unavailable.
    """
    encoder = _token_encoder()
    if encoder is None:
        return None

    try:
        from backend.Assistant import AssistantPrompts  # noqa: PLC0415
        from backend.AssistantTools import (  # noqa: PLC0415
            AnalyzeResultsTool,
            DelegateTool,
            DocumentationTool,
            FindRelevantReports,
            ReadReportTool,
            SearchTool,
            SkillsTool,
        )

        general_info = AssistantPrompts.general_info(
            columns=[],
            rows=0,
            last_updated="unknown",
        )
        tool_classes = (
            SkillsTool,
            SearchTool,
            ReadReportTool,
            DocumentationTool,
            AnalyzeResultsTool,
            FindRelevantReports,
            DelegateTool,
        )
        tools = [
            object.__new__(tool_class).to_openai_format() for tool_class in tool_classes
        ]
        tool_tokens = len(
            encoder.encode(
                json.dumps(tools, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        return {
            "plan": len(
                encoder.encode(AssistantPrompts.orient_plan_system(general_info)),
            )
            + tool_tokens,
            "act": len(encoder.encode(AssistantPrompts.act_system(general_info)))
            + tool_tokens,
            "tool_schema_tokens": tool_tokens,
        }
    except Exception:  # noqa: BLE001 - cost display must not break the viewer
        return None


def _estimate_conversation_cost(history: list[Any]) -> dict[str, Any]:
    """Estimate the USD cost of the API calls represented by a history.

    The stored transcript is replayed in order so repeated context and large
    tool outputs are counted at each call where they were sent. The estimate
    cannot include hidden reasoning tokens, title generation, delegated
    analysis, embeddings/search calls, or cached-token discounts.

    Returns:
        dict[str, Any]: Token and cost estimate, or an unavailable marker.
    """
    encoder = _token_encoder()
    if encoder is None:
        return {
            "available": False,
            "reason": "tiktoken is not installed or the encoding could not be loaded.",
        }

    overhead = _call_overhead_tokens() or {}
    messages_tokens = 0
    input_tokens = 0
    output_tokens = 0
    plan_calls = 0
    act_calls = 0

    for raw_entry in history:
        entry = _normalise_entry(raw_entry)
        ai_items = _entry_ai_items(entry)
        if not ai_items:
            continue

        call_kind = _entry_call_kind(entry, ai_items)
        if call_kind:
            input_tokens += overhead.get(call_kind, 0) + messages_tokens
            output_tokens += _response_tokens(encoder, ai_items)
            if call_kind == "plan":
                plan_calls += 1
            else:
                act_calls += 1

        for ai_item in ai_items:
            messages_tokens += _item_tokens(encoder, ai_item)

    input_cost = (input_tokens / 1_000_000) * INPUT_COST_PER_MILLION
    output_cost = (output_tokens / 1_000_000) * OUTPUT_COST_PER_MILLION
    total_tokens = input_tokens + output_tokens

    return {
        "available": True,
        "currency": "USD",
        "model": os.getenv("OPENAI_MODEL", "unknown"),
        "encoding": COST_ENCODING_NAME,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "input_cost_usd": round(input_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd": round(input_cost + output_cost, 6),
        "input_cost_per_million": INPUT_COST_PER_MILLION,
        "output_cost_per_million": OUTPUT_COST_PER_MILLION,
        "api_calls": plan_calls + act_calls,
        "plan_calls": plan_calls,
        "act_calls": act_calls,
        "includes_prompt_overhead": bool(overhead),
        "tool_schema_tokens": overhead.get("tool_schema_tokens"),
        "note": (
            "Estimated from the stored transcript plus the current system prompts "
            "and tool schemas. Excludes reasoning tokens, title generation, "
            "delegated analysis, embedding/search charges, and cached-token "
            "discounts."
        ),
    }


def _is_internal_entry(item: Any) -> bool:
    """Return whether a history entry is a tool/planning step.

    Returns:
        bool: True for planning, tool calls, or tool results.
    """
    display = item.get("display", {}) if isinstance(item, dict) else {}
    metadata = display.get("metadata")
    title = str(metadata.get("title", "")) if isinstance(metadata, dict) else ""
    content = str(display.get("content", ""))
    return title.startswith(_INTERNAL_TITLE_PREFIXES) or content.startswith(
        ("🔧", "📖"),
    )


def _split_tool_content(content: str) -> tuple[str, str]:
    """Split a stored tool-call entry into its call and output.

    Returns:
        tuple[str, str]: Call text and tool output.
    """
    marker = "\n|\nv\n"
    if marker in content:
        call, output = content.split(marker, 1)
        return call.strip(), output.strip()
    return content.strip(), ""


def _conversation_messages(
    history: list[Any],
    *,
    include_internal: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Convert stored history entries to display messages.

    Internal planning and tool steps are returned as compact, expandable
    entries so the UI can collapse them by default.

    Returns:
        tuple: Display messages and the number of hidden internal steps.
    """
    messages: list[dict[str, Any]] = []
    hidden_internal = 0
    for raw_item in history:
        item = _normalise_entry(raw_item)
        display = item.get("display", {}) if isinstance(item, dict) else {}
        internal = _is_internal_entry(item)
        if internal and not include_internal:
            hidden_internal += 1
            continue

        role = display.get("role", "assistant")
        if role not in {"user", "assistant"}:
            role = "assistant"
        content = str(display.get("content", ""))
        metadata = display.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        title = str(metadata.get("title", ""))
        call, output = _split_tool_content(content) if internal else ("", "")
        planning = internal and (title.startswith("🧠") or content.startswith("🧠"))

        messages.append(
            {
                "role": role,
                "content": content,
                "internal": internal,
                "kind": "planning" if planning else ("tool" if internal else "message"),
                "title": title or ("Planning" if planning else ""),
                "call": call,
                "output": output,
                "status": str(metadata.get("status", "")),
            },
        )
    return messages, hidden_internal


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Return the local viewer page.

    Returns:
        str: HTML page.
    """
    return PAGE


@app.get("/api/usage")
def usage(
    include_deleted: bool = Query(default=False),  # noqa: FBT001
) -> JSONResponse:
    """Return usage totals and conversation metadata.

    Returns:
        JSONResponse: Summary and conversation list.

    Raises:
        HTTPException: When Azure Storage cannot be read.
    """
    try:
        return JSONResponse(_usage_payload(include_deleted))
    except HttpResponseError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Unable to read conversation metadata. Ensure `az login` is active "
                "and your user has Storage Table Data Reader/Contributor and "
                f"Storage Blob Data Reader/Contributor. {type(exc).__name__}: {exc}"
            ),
        ) from exc


@app.get("/api/conversation")
def conversation(
    blob: str = Query(min_length=1),
    include_internal: bool = Query(default=False),  # noqa: FBT001
) -> JSONResponse:
    """Return one conversation transcript.

    Returns:
        JSONResponse: Messages, hidden-step count, and raw JSON.

    Raises:
        HTTPException: When the blob cannot be read.
    """
    try:
        payload = (
            BLOB_CLIENT.get_blob_client(blob).download_blob().readall().decode("utf-8")
        )
        history = json.loads(payload)
    except HttpResponseError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Unable to read the conversation blob. Ensure `az login` is active "
                "and your user has Storage Blob Data Reader/Contributor. "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid conversation JSON: {exc}",
        ) from exc

    if not isinstance(history, list):
        raise HTTPException(status_code=422, detail="Unexpected conversation format.")

    messages, hidden_internal = _conversation_messages(
        history,
        include_internal=include_internal,
    )
    try:
        cost = _estimate_conversation_cost(history)
    except Exception as exc:  # noqa: BLE001 - leave the transcript readable
        cost = {
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    return JSONResponse(
        {
            "messages": messages,
            "hidden_internal": hidden_internal,
            "total_entries": len(history),
            "cost": cost,
            "raw": json.dumps(history, indent=2, ensure_ascii=False),
        },
    )


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TAIC Conversation Viewer</title>
<style>
  :root {
    --bg: #f5f7fb;
    --surface: #ffffff;
    --surface-2: #eef2f8;
    --line: #dbe2ec;
    --text: #172033;
    --muted: #64748b;
    --accent: #1f5f99;
    --accent-2: #e7eef8;
    --user: #e8f1fb;
    --assistant: #f3f5f8;
    --danger: #b42318;
    --danger-bg: #fef3f2;
    --shadow: 0 8px 28px rgba(31, 45, 61, 0.08);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    display: flex; flex-direction: column; overflow: hidden;
  }
  header {
    flex: 0 0 auto;
    display: flex; justify-content: space-between; align-items: center; gap: 16px;
    padding: 10px 20px; background: var(--surface); border-bottom: 1px solid var(--line);
  }
  h1 { margin: 0; font-size: 18px; letter-spacing: -0.02em; }
  .subtitle { color: var(--muted); font-size: 12px; margin-top: 1px; }
  button, input { font: inherit; }
  button {
    cursor: pointer; border: 1px solid var(--line); background: var(--surface);
    color: var(--text); border-radius: 7px; padding: 6px 10px; font-size: 13px;
  }
  button:hover { background: var(--surface-2); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.primary:hover { filter: brightness(0.95); }
  button:disabled { opacity: 0.5; cursor: default; }
  main {
    flex: 1; min-height: 0; width: 100%; max-width: none;
    margin: 0 auto; padding: 10px 20px 14px;
    display: flex; flex-direction: column;
  }
  .summary-bar { flex: 0 0 auto; }
  .stats { display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: center; }
  .stat {
    display: flex; align-items: baseline; gap: 6px;
    background: transparent; border: 0; padding: 0; box-shadow: none;
  }
  .stat .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
  .stat .value { font-size: 15px; font-weight: 700; margin: 0; }
  .toolbar { display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; flex: 0 0 auto; }
  .toolbar input[type="search"] {
    flex: 1; min-width: 200px; padding: 6px 10px; border: 1px solid var(--line);
    border-radius: 7px; background: var(--surface); font-size: 13px;
  }
  .toggle { display: flex; align-items: center; gap: 5px; color: var(--muted); font-size: 12px; white-space: nowrap; }
  .internal-note { margin: 0; color: var(--muted); font-size: 12px; }
  .layout {
    flex: 1; min-height: 0;
    display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 12px;
    align-items: stretch;
  }
  .panel {
    background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
    box-shadow: var(--shadow); overflow: hidden;
    display: flex; flex-direction: column; min-height: 0;
  }
  .panel-head {
    flex: 0 0 auto;
    padding: 8px 12px; border-bottom: 1px solid var(--line); font-weight: 650;
    display: flex; justify-content: space-between; align-items: center; gap: 8px;
  }
  #detail-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #copy-conversation { flex: 0 0 auto; padding: 4px 10px; font-size: 12px; white-space: nowrap; }
  .list { flex: 1; min-height: 0; overflow: auto; max-height: none; }
  .conversation {
    padding: 9px 12px; border-bottom: 1px solid var(--line); cursor: pointer;
  }
  .conversation:hover { background: var(--surface-2); }
  .conversation.active { background: var(--accent-2); border-left: 3px solid var(--accent); }
  .conversation .title { font-weight: 620; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .conversation .meta { display: flex; gap: 8px; color: var(--muted); font-size: 12px; margin-top: 3px; flex-wrap: wrap; }
  .detail { min-height: 0; }
  .detail-body {
    flex: 1; min-height: 0; padding: 10px 12px 12px;
    display: flex; flex-direction: column; gap: 8px;
  }
  details.meta-details {
    margin: 0; flex: 0 0 auto;
    background: var(--surface-2); border: 1px solid var(--line);
    border-radius: 8px; overflow: hidden;
  }
  details.meta-details > summary {
    list-style: none; display: flex; gap: 14px; flex-wrap: wrap; align-items: center;
    padding: 6px 10px; cursor: pointer; color: var(--muted); font-size: 12px;
  }
  details.meta-details > summary::-webkit-details-marker { display: none; }
  details.meta-details > summary strong { color: var(--text); font-weight: 600; }
  details.meta-details[open] > summary { border-bottom: 1px solid var(--line); }
  .meta-grid {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px 16px;
    margin: 0; padding: 8px 10px 10px;
  }
  .meta-grid div { color: var(--muted); font-size: 12px; }
  .meta-grid strong { color: var(--text); font-weight: 600; }
  .cost-sub { color: var(--muted); font-size: 11px; }
  .cost-note {
    padding: 0 10px 10px; color: var(--muted); font-size: 11px; line-height: 1.45;
  }
  .cost-note strong { color: var(--text); }
  .messages {
    flex: 1; min-height: 0; max-height: none;
    display: flex; flex-direction: column; gap: 8px; overflow: auto; padding-right: 4px;
  }
  .message { border-radius: 9px; padding: 8px 11px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .message.user { background: var(--user); margin-left: 10%; }
  .message.assistant { background: var(--assistant); margin-right: 10%; }
  .message .role { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 3px; }
  details.activity-group {
    margin: 0; background: var(--surface); border: 1px solid var(--line);
    border-radius: 9px; overflow: hidden;
  }
  details.activity-group > summary {
    list-style: none; display: flex; align-items: center; gap: 7px;
    padding: 6px 9px; cursor: pointer; color: var(--muted);
    font-size: 12px; font-weight: 600; user-select: none;
  }
  details.activity-group > summary::-webkit-details-marker { display: none; }
  details.activity-group > summary:hover { background: var(--surface-2); }
  .activity-names {
    color: var(--muted); font-weight: 400; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
  }
  .activity-body { padding: 0 6px 6px; display: flex; flex-direction: column; gap: 5px; }
  details.tool-step {
    margin: 0; background: var(--assistant); border: 1px solid var(--line);
    border-radius: 7px; overflow: hidden;
  }
  details.tool-step > summary {
    list-style: none; display: flex; align-items: center; gap: 7px;
    padding: 5px 8px; cursor: pointer; font-size: 12px; user-select: none;
  }
  details.tool-step > summary::-webkit-details-marker { display: none; }
  details.tool-step > summary:hover { background: var(--surface-2); }
  details.tool-step[open] > summary { border-bottom: 1px solid var(--line); }
  details.tool-step[open] > summary .tool-preview { display: none; }
  .tool-name { font-weight: 600; white-space: nowrap; }
  .tool-preview {
    color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    flex: 1; min-width: 0;
  }
  .tool-status { color: var(--accent); font-size: 11px; white-space: nowrap; }
  .chev { margin-left: auto; color: var(--muted); transition: transform 0.15s ease; }
  details[open] > summary .chev { transform: rotate(90deg); }
  .tool-body { padding: 8px; display: flex; flex-direction: column; gap: 7px; }
  .tool-body .label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; }
  .tool-args, .tool-output {
    margin: 0; padding: 7px 9px; background: var(--surface);
    border: 1px solid var(--line); border-radius: 6px;
    white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto;
    max-height: 34vh; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .tool-output { background: #f8fafc; }
  details.raw-details { margin: 0; flex: 0 0 auto; }
  details.raw-details > summary { cursor: pointer; color: var(--accent); font-weight: 600; font-size: 12px; }
  pre { background: #111827; color: #e5e7eb; border-radius: 8px; padding: 10px; overflow: auto; max-height: 34vh; font-size: 12px; margin: 5px 0 0; }
  .error {
    display: none; margin: 0; padding: 8px 10px; border-radius: 8px;
    background: var(--danger-bg); color: var(--danger); border: 1px solid #fecdca;
    white-space: pre-wrap; font-size: 12px;
  }
  .empty { padding: 20px; color: var(--muted); }
  .top-users { margin-top: 3px; color: var(--muted); font-size: 11px; }
  .top-users span { color: var(--text); font-weight: 600; }
  body.focus .summary-bar, body.focus .list-panel { display: none; }
  body.focus .layout { grid-template-columns: minmax(0, 1fr); }
  @media (max-width: 1000px) {
    body { height: auto; overflow: auto; }
    main { display: block; }
    .stats { gap: 6px 14px; }
    .layout { display: flex; flex-direction: column; }
    .list-panel { max-height: 36vh; }
    .detail { min-height: 62vh; }
    .meta-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
</style>
</head>
<body>
<header>
  <div>
    <h1>TAIC Conversation Viewer</h1>
    <div class="subtitle">Local only · reads with your az login identity · no storage keys · times in NZT (Pacific/Auckland)</div>
  </div>
  <button class="primary" id="refresh">Refresh</button>
</header>
<main>
  <div class="error" id="error"></div>
  <div class="summary-bar">
    <section class="stats">
      <div class="stat"><div class="label">Conversations</div><div class="value" id="stat-conversations">—</div></div>
      <div class="stat"><div class="label">Users</div><div class="value" id="stat-users">—</div></div>
      <div class="stat"><div class="label">Stored messages</div><div class="value" id="stat-messages">—</div></div>
      <div class="stat"><div class="label">Last 7 days</div><div class="value" id="stat-recent">—</div></div>
      <div class="stat" title="Only includes conversations opened in this session">
        <div class="label">Loaded cost</div><div class="value" id="stat-cost">—</div>
      </div>
    </section>
    <div class="top-users" id="top-users"></div>
  </div>
  <div class="toolbar">
    <input id="filter" type="search" placeholder="Filter by title, user, or conversation ID…">
    <label class="toggle"><input id="include-deleted" type="checkbox"> Include deleted</label>
    <label class="toggle"><input id="show-internal" type="checkbox" checked> Tool calls &amp; planning</label>
    <button id="expand-tools" title="Expand every tool call and planning step">Expand all</button>
    <button id="collapse-tools" title="Collapse every tool call and planning step">Collapse all</button>
    <button id="focus" title="Hide the list and statistics to maximise the transcript">Focus</button>
  </div>
  <div class="layout">
    <section class="panel list-panel">
      <div class="panel-head"><span>Conversations</span><span id="list-count"></span></div>
      <div class="list" id="list"></div>
    </section>
    <section class="panel detail">
      <div class="panel-head">
        <span id="detail-title">Select a conversation</span>
        <button id="copy-conversation" disabled
          title="Copy a Markdown transcript of all messages, tool calls and planning, with large tool outputs omitted">Copy conversation</button>
      </div>
      <div class="detail-body" id="detail">
        <div class="empty">Choose a conversation on the left to inspect it.</div>
      </div>
    </section>
  </div>
</main>
<script>
  let conversations = [];
  let selected = null;
  const COPY_TOOL_LIMIT = 2000;
  const costCache = new Map();

  const $ = (id) => document.getElementById(id);

  function showError(message) {
    const box = $("error");
    box.textContent = message || "";
    box.style.display = message ? "block" : "none";
  }

  function fmt(value) {
    return value === null || value === undefined || value === "" ? "—" : String(value);
  }

  function formatMoney(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    const amount = Number(value);
    if (amount === 0) return "$0.00";
    if (amount < 0.0001) return "<$0.0001";
    return amount >= 100 ? `$${amount.toFixed(2)}` : `$${amount.toFixed(4)}`;
  }

  function formatNumber(value) {
    return value === null || value === undefined || Number.isNaN(Number(value))
      ? "—"
      : Number(value).toLocaleString();
  }

  function renderLoadedCost() {
    const loaded = conversations.filter(
      (conversation) => conversation.cost && conversation.cost.available !== false
    );
    const total = loaded.reduce(
      (sum, conversation) => sum + Number(conversation.cost.total_cost_usd || 0),
      0
    );
    const value = $("stat-cost");
    value.textContent = loaded.length ? formatMoney(total) : "—";
    value.title = loaded.length
      ? `Estimated cost for ${loaded.length} of ${conversations.length} conversations opened in this session.`
      : "Open a conversation to estimate its cost.";
  }

  function rememberCost(conversation, cost) {
    if (!cost) return;
    conversation.cost = cost;
    if (cost.available !== false) costCache.set(conversation.blob_name, cost);
    renderList();
  }

  async function getJSON(url) {
    const response = await fetch(url);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || `${response.status} ${response.statusText}`);
    }
    return body;
  }

  function renderStats(summary) {
    $("stat-conversations").textContent = summary.conversations;
    $("stat-users").textContent = summary.users;
    $("stat-messages").textContent = summary.messages;
    $("stat-recent").textContent = summary.recent_conversations;
    const top = (summary.top_users || []).slice(0, 5).map(
      (u) => `${u.user} (${u.conversations})`
    );
    $("top-users").innerHTML = top.length
      ? "Top users: " + top.map((t) => `<span>${escapeHTML(t)}</span>`).join(" · ")
      : "";
  }

  function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[char]));
  }

  function filtered() {
    const query = $("filter").value.trim().toLowerCase();
    if (!query) return conversations;
    return conversations.filter((c) =>
      [c.title, c.user, c.id].some((value) => String(value || "").toLowerCase().includes(query))
    );
  }

  function renderList() {
    const list = $("list");
    const rows = filtered();
    $("list-count").textContent = `${rows.length} of ${conversations.length}`;
    renderLoadedCost();
    list.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No conversations match.";
      list.append(empty);
      return;
    }
    for (const conversation of rows) {
      const item = document.createElement("div");
      item.className = "conversation" + (selected?.blob_name === conversation.blob_name ? " active" : "");
      item.dataset.blob = conversation.blob_name;
      const updated = conversation.last_updated_nzt || conversation.last_updated || "";
      item.innerHTML = `
        <div class="title">${escapeHTML(conversation.title || "Untitled")}</div>
        <div class="meta">
          <span>${escapeHTML(conversation.user || "")}</span>
          <span title="Stored UTC: ${escapeHTML(conversation.last_updated || "")}">${escapeHTML(updated)}</span>
          <span>${conversation.messages} msgs</span>
          ${conversation.cost && conversation.cost.available !== false
            ? `<span title="Estimated API cost">${escapeHTML(formatMoney(conversation.cost.total_cost_usd))}</span>`
            : ""}
          ${conversation.deleted ? "<span>deleted</span>" : ""}
        </div>`;
      item.addEventListener("click", () => selectConversation(conversation));
      list.append(item);
    }
  }

  function renderDetailMeta(conversation) {
    const cost = conversation.cost;
    const costLoaded = cost && cost.available !== false;
    const costNote = costLoaded
      ? `<div class="cost-note"><strong>Estimated API cost.</strong> ${escapeHTML(cost.note || "")} Model: <strong>${escapeHTML(fmt(cost.model))}</strong>. Encoding: <strong>${escapeHTML(fmt(cost.encoding))}</strong>.${cost.includes_prompt_overhead === false ? " System prompt and tool schema overhead could not be loaded." : ""}</div>`
      : "";
    const costSummary = costLoaded
      ? `<span>Est. cost <strong>${escapeHTML(formatMoney(cost.total_cost_usd))}</strong></span>`
      : "";
    const costReason = cost && cost.available === false
      ? escapeHTML(cost.reason || "Cost estimate unavailable.")
      : "loading…";
    return `
      <details class="meta-details">
        <summary>
          <span>User <strong>${escapeHTML(fmt(conversation.user))}</strong></span>
          <span>Updated <strong title="Stored UTC: ${escapeHTML(fmt(conversation.last_updated))}">${escapeHTML(fmt(conversation.last_updated_nzt || conversation.last_updated))}</strong></span>
          <span><strong>${escapeHTML(fmt(conversation.messages))}</strong> messages</span>
          ${costSummary}
          <span>v<strong>${escapeHTML(fmt(conversation.app_version))}</strong></span>
          <span class="chev">&rsaquo;</span>
        </summary>
        <div class="meta-grid">
          <div>User<br><strong>${escapeHTML(fmt(conversation.user))}</strong></div>
          <div>Last updated (NZT)<br><strong title="Stored UTC: ${escapeHTML(fmt(conversation.last_updated))}">${escapeHTML(fmt(conversation.last_updated_nzt || conversation.last_updated))}</strong></div>
          <div>Created (NZT)<br><strong title="Stored UTC: ${escapeHTML(fmt(conversation.created_at))}">${escapeHTML(fmt(conversation.created_at_nzt || conversation.created_at))}</strong></div>
          <div>Messages<br><strong>${escapeHTML(fmt(conversation.messages))}</strong></div>
          <div>Est. cost<br><strong>${costLoaded ? escapeHTML(formatMoney(cost.total_cost_usd)) : costReason}</strong><br><span class="cost-sub">${costLoaded ? `${escapeHTML(formatMoney(cost.input_cost_usd))} in + ${escapeHTML(formatMoney(cost.output_cost_usd))} out` : ""}</span></div>
          <div>Est. tokens<br><strong>${costLoaded ? escapeHTML(formatNumber(cost.total_tokens)) : "—"}</strong><br><span class="cost-sub">${costLoaded ? `${escapeHTML(formatNumber(cost.input_tokens))} in / ${escapeHTML(formatNumber(cost.output_tokens))} out` : ""}</span></div>
          <div>API calls<br><strong>${costLoaded ? escapeHTML(formatNumber(cost.api_calls)) : "—"}</strong><br><span class="cost-sub">${costLoaded ? `${escapeHTML(formatNumber(cost.plan_calls))} plan + ${escapeHTML(formatNumber(cost.act_calls))} act` : ""}</span></div>
          <div>Pricing<br><strong>${costLoaded ? `$${escapeHTML(fmt(cost.input_cost_per_million))}/M in` : "—"}</strong><br><span class="cost-sub">${costLoaded ? `$${escapeHTML(fmt(cost.output_cost_per_million))}/M out` : ""}</span></div>
          <div>App version<br><strong>${escapeHTML(fmt(conversation.app_version))}</strong></div>
          <div>DB version<br><strong>${escapeHTML(fmt(conversation.db_version))}</strong></div>
          <div>Conversation ID<br><strong>${escapeHTML(fmt(conversation.id))}</strong></div>
          <div>Deleted<br><strong>${conversation.deleted ? "yes" : "no"}</strong></div>
        </div>
        ${costNote}
      </details>`;
  }

  function toolLabel(message) {
    const text = String(message.title || message.call || message.content || "Tool call");
    return text.replace(/^[^\w]+/, "").replace(/\s*✓\s*$/, "").trim() || "Tool call";
  }

  function callArguments(message) {
    const call = String(message.call || "");
    const open = call.indexOf("(");
    if (open === -1) return "";
    let args = call.slice(open + 1).trim();
    if (args.endsWith(")")) args = args.slice(0, -1).trim();
    return args;
  }

  function appendToolSection(body, label, text, className) {
    if (!text) return;
    const labelElement = document.createElement("div");
    labelElement.className = "label";
    labelElement.textContent = label;
    const content = document.createElement("div");
    content.className = className;
    content.textContent = text;
    body.append(labelElement, content);
  }

  function createToolStep(message) {
    const details = document.createElement("details");
    details.className = "tool-step";
    if (message.status === "pending") details.open = true;

    const summary = document.createElement("summary");
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = message.title || toolLabel(message);
    summary.append(name);

    const args = callArguments(message);
    if (args) {
      const preview = document.createElement("span");
      preview.className = "tool-preview";
      preview.textContent = args;
      preview.title = args;
      summary.append(preview);
    }
    if (message.status === "pending") {
      const status = document.createElement("span");
      status.className = "tool-status";
      status.textContent = "running…";
      summary.append(status);
    }
    const chevron = document.createElement("span");
    chevron.className = "chev";
    chevron.textContent = "\u203a";
    summary.append(chevron);
    details.append(summary);

    const body = document.createElement("div");
    body.className = "tool-body";
    if (message.kind === "planning") {
      appendToolSection(body, "Planning", message.content || "(no content)", "tool-output");
    } else {
      appendToolSection(body, "Arguments", args || message.call || "(none)", "tool-args");
      if (message.output) {
        appendToolSection(body, "Output", message.output, "tool-output");
      } else if (message.status !== "pending") {
        appendToolSection(body, "Output", "(no output)", "tool-output");
      }
    }
    details.append(body);
    return details;
  }

  function createActivityGroup(items) {
    const details = document.createElement("details");
    details.className = "activity-group";
    if (items.some((item) => item.status === "pending")) details.open = true;

    const summary = document.createElement("summary");
    const icon = document.createElement("span");
    icon.textContent = "⚙️";
    const label = document.createElement("span");
    const allTools = items.every((item) => item.kind === "tool");
    label.textContent = `${items.length} ${allTools ? "tool step" : "step"}${items.length === 1 ? "" : "s"}`;
    const names = document.createElement("span");
    names.className = "activity-names";
    const uniqueNames = [...new Set(items.map(toolLabel).filter(Boolean))];
    names.textContent = uniqueNames.slice(0, 4).join(" · ") +
      (uniqueNames.length > 4 ? ` +${uniqueNames.length - 4}` : "");
    const chevron = document.createElement("span");
    chevron.className = "chev";
    chevron.textContent = "\u203a";
    summary.append(icon, label, names, chevron);
    details.append(summary);

    const body = document.createElement("div");
    body.className = "activity-body";
    for (const item of items) body.append(createToolStep(item));
    details.append(body);
    return details;
  }

  function renderMessages(container, messages) {
    let activity = [];
    const flushActivity = () => {
      if (!activity.length) return;
      container.append(
        activity.length === 1 ? createToolStep(activity[0]) : createActivityGroup(activity),
      );
      activity = [];
    };
    for (const message of messages) {
      if (message.internal) {
        activity.push(message);
        continue;
      }
      flushActivity();
      const item = document.createElement("div");
      item.className = `message ${message.role}`;
      const role = document.createElement("div");
      role.className = "role";
      role.textContent = message.role;
      const content = document.createElement("div");
      content.textContent = message.content;
      item.append(role, content);
      container.append(item);
    }
    flushActivity();
  }

  function truncateText(value, limit) {
    const text = String(value ?? "");
    if (text.length <= limit) return text;
    const remaining = text.length - limit;
    return `${text.slice(0, limit).trimEnd()}… [truncated ${remaining.toLocaleString()} characters]`;
  }

  function fencedBlock(value) {
    const text = String(value ?? "");
    let longest = 0;
    for (const run of text.match(/`+/g) || []) longest = Math.max(longest, run.length);
    const fence = "`".repeat(Math.max(3, longest + 1));
    return `${fence}text\n${text}\n${fence}`;
  }

  function buildTranscript(conversation, messages) {
    const lines = [];
    lines.push(`# ${conversation.title || "Untitled conversation"}`, "");
    lines.push(`- User: ${conversation.user || "unknown"}`);
    if (conversation.created_at_nzt || conversation.created_at) {
      lines.push(`- Created: ${conversation.created_at_nzt || conversation.created_at}`);
    }
    lines.push(`- Last updated: ${conversation.last_updated_nzt || conversation.last_updated || "unknown"}`);
    lines.push(`- Conversation ID: ${conversation.id || "unknown"}`);
    if (conversation.app_version) lines.push(`- App version: ${conversation.app_version}`);
    const cost = conversation.cost;
    if (cost && cost.available !== false) {
      lines.push(
        `- Estimated cost: ${formatMoney(cost.total_cost_usd)} ` +
        `(${formatNumber(cost.total_tokens)} tokens: ` +
        `${formatNumber(cost.input_tokens)} in / ${formatNumber(cost.output_tokens)} out)`
      );
    }
    lines.push(
      "",
      `_Tool arguments are truncated and tool outputs longer than ${COPY_TOOL_LIMIT.toLocaleString()} characters are omitted._`,
      "",
      "---",
      "",
    );

    for (const message of messages) {
      if (message.internal) {
        if (message.kind === "planning") {
          lines.push("### 🧠 Planning", "", String(message.content || "").trim(), "");
          continue;
        }
        const name = toolLabel(message) || "Tool call";
        lines.push(`### 🔧 Tool call: ${name}`, "");
        const args = callArguments(message);
        if (args) {
          lines.push(
            "Arguments:",
            "",
            fencedBlock(truncateText(args, COPY_TOOL_LIMIT)),
            "",
          );
        } else if (message.call) {
          lines.push(fencedBlock(message.call), "");
        }
        const output = String(message.output || "");
        if (!output) {
          lines.push("_No output._", "");
        } else if (output.length > COPY_TOOL_LIMIT) {
          lines.push(`_Output omitted (${output.length.toLocaleString()} characters)._`, "");
        } else {
          lines.push("Output:", "", fencedBlock(output), "");
        }
        continue;
      }
      const role = message.role === "user" ? "User" : "Assistant";
      lines.push(`## ${role}`, "", String(message.content || "").trim(), "");
    }

    return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
  }

  async function writeClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    document.body.append(area);
    area.select();
    try {
      if (!document.execCommand("copy")) {
        throw new Error("the browser rejected the copy command");
      }
    } finally {
      area.remove();
    }
  }

  async function copyConversation() {
    const conversation = selected;
    if (!conversation) return;
    const button = $("copy-conversation");
    button.disabled = true;
    button.textContent = "Copying…";
    try {
      const data = await getJSON(
        `/api/conversation?blob=${encodeURIComponent(conversation.blob_name)}` +
        `&include_internal=true`
      );
      rememberCost(conversation, data.cost);
      await writeClipboard(buildTranscript(conversation, data.messages));
      button.textContent = "Copied ✓";
      showError("");
    } catch (error) {
      button.textContent = "Copy failed";
      showError(`Could not copy the conversation: ${error.message}`);
    }
    setTimeout(() => {
      button.textContent = "Copy conversation";
      button.disabled = !selected;
    }, 1600);
  }

  async function selectConversation(conversation) {
    selected = conversation;
    $("copy-conversation").disabled = false;
    renderList();
    $("detail-title").textContent = conversation.title || "Conversation";
    $("detail").innerHTML = renderDetailMeta(conversation) + '<div class="empty">Loading transcript…</div>';
    try {
      const includeInternal = $("show-internal").checked;
      const data = await getJSON(
        `/api/conversation?blob=${encodeURIComponent(conversation.blob_name)}` +
        `&include_internal=${includeInternal}`
      );
      rememberCost(conversation, data.cost);
      $("detail").innerHTML = renderDetailMeta(conversation);
      if (data.hidden_internal) {
        const note = document.createElement("div");
        note.className = "internal-note";
        note.textContent =
          `${data.hidden_internal} tool/planning step(s) hidden. ` +
          'Tick "Tool calls & planning" to show them collapsed.';
        $("detail").append(note);
      }
      const messages = document.createElement("div");
      messages.className = "messages";
      if (!data.messages.length) {
        messages.innerHTML = '<div class="empty">No conversation entries to show.</div>';
      } else {
        renderMessages(messages, data.messages);
      }
      const rawDetails = document.createElement("details");
      rawDetails.className = "raw-details";
      const summary = document.createElement("summary");
      summary.textContent = "Raw conversation JSON";
      const pre = document.createElement("pre");
      pre.textContent = data.raw;
      rawDetails.append(summary, pre);
      $("detail").append(messages, rawDetails);
    } catch (error) {
      $("detail").innerHTML = renderDetailMeta(conversation) +
        `<div class="error" style="display:block">${escapeHTML(error.message)}</div>`;
    }
  }

  async function loadUsage() {
    showError("");
    $("refresh").disabled = true;
    try {
      const data = await getJSON(`/api/usage?include_deleted=${$("include-deleted").checked}`);
      conversations = (data.conversations || []).map((conversation) => ({
        ...conversation,
        cost: costCache.get(conversation.blob_name) || null,
      }));
      renderStats(data.summary);
      renderList();
      if (selected) {
        const stillThere = conversations.find((c) => c.blob_name === selected.blob_name);
        if (stillThere) {
          selected = stillThere;
        } else {
          selected = null;
          $("copy-conversation").disabled = true;
          $("detail-title").textContent = "Select a conversation";
          $("detail").innerHTML = '<div class="empty">Choose a conversation on the left to inspect it.</div>';
        }
      }
    } catch (error) {
      showError(error.message);
    } finally {
      $("refresh").disabled = false;
    }
  }

  $("refresh").addEventListener("click", loadUsage);
  $("copy-conversation").addEventListener("click", copyConversation);
  $("include-deleted").addEventListener("change", loadUsage);
  $("show-internal").addEventListener("change", () => {
    if (selected) selectConversation(selected);
  });
  $("expand-tools").addEventListener("click", () => {
    for (const details of $("detail").querySelectorAll("details.activity-group, details.tool-step")) {
      details.open = true;
    }
  });
  $("collapse-tools").addEventListener("click", () => {
    for (const details of $("detail").querySelectorAll("details.activity-group, details.tool-step")) {
      details.open = false;
    }
  });
  $("focus").addEventListener("click", () => {
    const focused = document.body.classList.toggle("focus");
    $("focus").textContent = focused ? "Exit focus" : "Focus";
    $("focus").title = focused
      ? "Show the conversation list and statistics again"
      : "Hide the list and statistics to maximise the transcript";
  });
  $("filter").addEventListener("input", renderList);
  loadUsage();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7861)
