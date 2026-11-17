"""Tools that the AI assistant can use to search, read reports, and reason."""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

from .Searching import Searcher, SearchParams

logger = logging.getLogger(__name__)


class Tool(ABC):
    """Base class for all tools.

    Subclasses set _tool_name, _tool_description, and _tool_parameters
    as class attributes instead of overriding properties.
    """

    _tool_name: str = ""
    _tool_description: str = ""
    _tool_parameters: ClassVar[dict[str, Any]] = {}

    @property
    def name(self) -> str:
        """Name of the tool.

        Returns:
            str: Name of the tool.
        """
        return self._tool_name

    @property
    def description(self) -> str:
        """Description of the tool.

        Returns:
            str: Description of the tool.
        """
        return self._tool_description

    @property
    def parameters(self) -> dict[str, Any]:
        """Parameters of the tool.

        Returns:
            dict: Parameters of the tool.
        """
        return self._tool_parameters

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Execute the tool with given parameters and return result as string."""

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI tool format.

        Returns:
            dict: Return the tool in OpenAI format.
        """
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def _normalise_search_args(search_args: dict[str, Any]) -> dict[str, Any]:
    """Normalise shared search arguments.

    Args:
        search_args: Raw search parameters.

    Returns:
        dict[str, Any]: Parameters with a sensible search_type default.
    """
    args = dict(search_args)
    query = args.get("query") or ""
    args["query"] = query
    if not query:
        args["search_type"] = None
    elif args.get("search_type") not in {"fts", "vector"}:
        args["search_type"] = "fts"
    return args


def _execute_search(
    searcher: Searcher,
    search_args: dict[str, Any],
    *,
    limit: int,
    username: str | None,
) -> tuple[SearchParams, pd.DataFrame, dict]:
    """Run a knowledge search using shared argument normalisation.

    Args:
        searcher: Searcher instance.
        search_args: Raw search parameters.
        limit: Maximum number of rows.
        username: User for log correlation.

    Returns:
        tuple: Parsed SearchParams, result DataFrame, and info dict.
    """
    params = SearchParams(**_normalise_search_args(search_args))
    results, info, _plots = searcher.knowledge_search(
        params,
        limit=limit,
        username=username,
    )
    return params, results, info


class SearchTool(Tool):
    """Tool for searching the knowledge base."""

    _tool_name = "search"
    _tool_description = """Search the all_document_types table (~145k rows) containing safety issues, recommendations, report sections, and summaries from TAIC, ATSB, and TSB.
Use query="" (empty string) for filter-only searches — this returns all results matching the filters without a text relevance ranking.
Document types:

- safety_issue — AI extraction from report text + website scraping for ATSB post-2008. All agencies.
  TAIC: confident extraction. TSB: inferred from "findings as to risk". ATSB post-2008: scraped; pre-2008: best-effort.
- recommendation — website scraping for TSB/TAIC; AI extraction for ATSB. All agencies.
  TSB/TAIC: scraped from websites. ATSB: AI extracted (confident); context/recipient/made fields are best-effort.
- section — AI extraction. All agencies. Report text chunked by page/section from parsed PDF.
- summary — website scraping. TAIC and ATSB only. Brief overviews from agency report webpages.

For more quantitative analysis you might find that the analyze_results tool or the search_report_text tool are more useful. Particularly for metadata filtering (i.e counts of occurrences by aircraft types etc) using the search_report_text tool would be better.

To reduce context overhead when you only need metadata/counts/IDs (not the document text), set exclude_document=true. This omits the large 'document' column from results so you can retrieve many more rows within the context window.

Examples:
# Filter-only: find all helicopter safety issues since 2020
search(query="", year_range=[2020, 2026], document_type=["safety_issue"], modes=["0"], agencies=["TAIC", "ATSB", "TSB"], metadata_filter="aircraft_type=Helicopter")

# Filter-only: find all rail accidents since 2010
search(query="", year_range=[2010, 2023], document_type=["summary", "section"], modes=["1"], agencies=["TAIC", "ATSB"])

# Filter-only: rail accidents with fatalities
search(query="", year_range=[2000, 2026], document_type=["summary", "section", "safety_issue"], modes=["1"], agencies=["TAIC", "ATSB", "TSB"], fatalities_range=[1, 100])

# Filter-only: all accidents with 5+ fatalities (no upper bound)
search(query="", year_range=[2000, 2026], document_type=["summary", "section", "safety_issue"], modes=["0", "1", "2"], agencies=["TAIC", "ATSB", "TSB"], fatalities_range=[5, -1])

# Semantic search: Trying to find recommendations or safety issues that are simliar.
search(query="operate its ships against international and domestic requirements", search_type="vector", year_range=[2010, 2023], document_type=["safety_issue", "recommendation"], modes=["0"], agencies=["TAIC"])

# Filter with text — find helicopter accidents mentioning engine failure
search(query="engine failure", search_type="fts", year_range=[2020, 2026], document_type=["summary", "section"], modes=["0"], agencies=["TAIC", "ATSB", "TSB"], metadata_filter="aircraft_type=Helicopter")

# Filter-only by metadata: find piston-engine aircraft accidents
search(query="", year_range=[2000, 2023], document_type=["safety_issue", "section"], modes=["0"], agencies=["TAIC", "ATSB"], metadata_filter="type_of_engines=piston")

# Filter-only metadata lookup without document text (low overhead, returns more rows)
search(query="", year_range=[2000, 2026], document_type=["safety_issue", "recommendation"], modes=["0", "1", "2"], agencies=["TAIC", "ATSB", "TSB"], exclude_document=true, limit=500)
"""
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query. Empty string returns all results matching other filters.",
            },
            "limit": {
                "type": "number",
                "description": "Maximum results to return. Default 150 which is good for quick overviews, larger (500+) for exhaustive retrieval. Set to 300 to help with broad analyses.",
            },
            "search_type": {
                "type": "string",
                "enum": ["fts", "vector"],
                "description": "Optional. fts for specific keyword matches (organisation names etc), vector for general semantic similarity. Omit for filter-only searches (query='').",
            },
            "year_range": {
                "type": "array",
                "description": f"Year range [start, end]. Valid range 2000-{datetime.now(tz=timezone.utc).year}.",
                "items": {"type": "number"},
            },
            "document_type": {
                "type": "array",
                "description": "Filter by document type: safety_issue, recommendation, section, or summary.",
                "items": {"type": "string"},
            },
            "modes": {
                "type": "array",
                "description": "Filter by mode: 0=aviation, 1=rail, 2=marine.",
                "items": {"type": "string"},
            },
            "agencies": {
                "type": "array",
                "description": "Filter by agency: TAIC, ATSB, or TSB.",
                "items": {"type": "string"},
            },
            "location": {
                "type": "string",
                "description": "Filter by location text (e.g. 'Broome', 'Wellington').",
            },
            "occurrence_type": {
                "type": "array",
                "description": "Filter by occurrence type (e.g. 'Engine failure or malfunction').",
                "items": {"type": "string"},
            },
            "fatalities_range": {
                "type": "array",
                "description": "Filter by fatalities count range, e.g. [1, 10] for 1 to 10 fatalities. Use -1 for no upper bound, e.g. [1, -1] for 1+ fatalities.",
                "items": {"type": "number"},
            },
            "injuries_range": {
                "type": "array",
                "description": "Filter by injuries count range, e.g. [0, 5] for 0 to 5 injuries. Use -1 for no upper bound, e.g. [3, -1] for 3+ injuries.",
                "items": {"type": "number"},
            },
            "metadata_filter": {
                "type": "string",
                "description": "Filter metadata_json. 'key=value' targets a field (e.g. 'aircraft.0.aircraft_type=Helicopter'); plain text searches entire JSON.",
            },
            "report_ids": {
                "type": "array",
                "description": "Filter by report IDs (e.g. ['ATSB_a_2000_648']).",
                "items": {"type": "string"},
            },
            "agency_ids": {
                "type": "array",
                "description": "Filter by agency IDs (e.g. ['AO-2000-003', '200002648']).",
                "items": {"type": "string"},
            },
            "exclude_document": {
                "type": "boolean",
                "description": "If true, exclude the large 'document' text column from results to reduce context overhead. Useful for filter-only / metadata exploration where you only need counts, IDs, or other columns. Default false (document included).",
                "default": False,
            },
        },
        "required": [
            "query",
            "year_range",
            "document_type",
            "modes",
            "agencies",
        ],
    }

    def __init__(self, searcher: Searcher):
        """Constructor."""
        self.searcher = searcher

    def execute(self, **kwargs) -> str:
        """Execute a search against the knowledge base.

        Returns:
            str: Markdown formatted search results with error details if search fails.
        """
        limit = kwargs.pop("limit", 150)
        exclude_document = kwargs.pop("exclude_document", False)
        # Username for log correlation (if propagated from app/Assistant)
        username = kwargs.pop("username", None) or kwargs.pop("user", None)

        tool_start = time.perf_counter()
        try:
            search_params, results, info = _execute_search(
                self.searcher,
                kwargs,
                limit=limit,
                username=username,
            )
        except Exception as e:
            elapsed = time.perf_counter() - tool_start
            logger.exception("SearchTool failed user=%s query=%r", username or "<none>", search_params.query)
            if isinstance(e, TimeoutError):
                return (
                    f"Search timed out after {elapsed:.1f}s (query={search_params.query!r}). "
                    f"Vector embedding via Azure AI likely hung. Error: {e}"
                )
            return f"Search failed after {elapsed:.1f}s: {e}"

        summary = f"**Search results:** {info.get('relevant_results', len(results))} relevant out of {len(results)} total"
        if info.get("info_message"):
            summary += f"\n_{info['info_message']}_"
        if exclude_document:
            summary += "\n_document column excluded (exclude_document=true) — reduced context overhead_"

        if "agency_id" in results.columns and "url" in results.columns:
            results["agency_id"] = results.apply(
                lambda row: f"<a href='{row['url']}'>{row['agency_id']}</a>" if not pd.isna(row["url"]) else row["agency_id"],
                axis=1,
            )

        results = results.drop(
            columns=[c for c in ["url", "year", "agency", "report_id"] if c in results.columns],
        )
        if exclude_document and "document" in results.columns:
            results = results.drop(columns=["document"])

        md = results.to_html(index=False, escape=False)
        return f"{summary}\n\n{md}"


class DocumentationTool(Tool):
    """Provides access to the smart tools platform documentation."""

    _tool_name = "documentation"
    _tool_description = "Provides access to the smart tools platform documentation. This includes information about the project itself (github readme) and the user documentation (which provides information for users on how to use the webapp)."
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "documents": {
                "type": "array",
                "description": "A list of documents to retrieve.",
                "items": {
                    "type": "string",
                    "enum": ["readme", "user-documentation"],
                },
                "minItems": 1,
                "uniqueItems": True,
            },
        },
        "required": ["documents"],
    }

    @staticmethod
    def execute(**kwargs) -> str:
        """Execute documentation retrieval.

        Returns:
            str: The requested documentation content.
        """
        requested_documents = kwargs.get("documents", [])

        if len(requested_documents) == 0:
            return "No documents specified."

        project_root = Path(__file__).parent.parent

        found_documents = []

        if "readme" in requested_documents:
            readme_path = project_root / "README.md"
            if readme_path.exists():
                with readme_path.open("r", encoding="utf-8") as f:
                    readme_content = f.read()
                found_documents.append(readme_content)
            else:
                found_documents.append("README file not found.")

        if "user-documentation" in requested_documents:
            user_doc_path = project_root / "static" / "user-documentation.html"
            if user_doc_path.exists():
                with user_doc_path.open("r", encoding="utf-8") as f:
                    user_doc_content = f.read()
                found_documents.append(user_doc_content)
            else:
                found_documents.append("User documentation file not found.")

        return "\n\n".join(found_documents)


class AnalysisError(ValueError):
    """Raised when an analysis operation is invalid or cannot be completed."""


# Reuse the search tool's schema rather than maintaining a duplicate copy.
ANALYZE_SEARCH_PARAMETERS = deepcopy(SearchTool._tool_parameters)  # noqa: SLF001
ANALYZE_SEARCH_PARAMETERS["description"] = (
    "Search parameters. Same fields as the search tool. The search runs inside "
    "this call."
)
ANALYZE_SEARCH_PARAMETERS["properties"]["limit"]["description"] = (
    "Maximum search rows to retrieve. Default 500."
)
ANALYZE_SEARCH_PARAMETERS["properties"]["exclude_document"]["description"] = (
    "Drop the document column before analysis. Default false."
)


class AnalyzeResultsTool(Tool):
    """Tool for running simple statistics on the last search results."""

    _tool_name = "analyze_results"
    _tool_description = """Run a search and calculate simple statistics on its results in one call.

Pass the same `search` object you would pass to the search tool, plus `fn` and
`column` (except for fn="size"). The search is executed internally, so no prior
search is required. Repeating a search is fine; it keeps tool state independent.

If `by` is omitted you get one number; if `by` is provided you get one row per group.

`fn` is one of: size, count, nunique, sum, mean, median, min, max, std. `size` counts rows and needs no column.
Optional `filters` maps a column to a value, a list (match any), or an operator object such as
{">": 0} or {"contains": "engine failure"}. Supported operators: ==, !=, >, >=, <, <=, in, not_in, contains.
`contains` is case-insensitive and works on text columns.
Optional `distinct_by` deduplicates rows before calculating, e.g. "report_id" to count each report once.
Optional `round` rounds numbers and `limit` caps grouped rows (default 50, max 200).

Useful columns: document, document_type, agency, mode, year, fatalities, injuries, report_id, occurrence_type, location.

Examples:
- Average fatalities per report: {"fn": "mean", "column": "fatalities", "distinct_by": "report_id"}
- Average fatalities in fatal accidents: {"fn": "mean", "column": "fatalities", "filters": {"fatalities": {">": 0}}, "distinct_by": "report_id"}
- Average fatalities in aviation: {"fn": "mean", "column": "fatalities", "filters": {"mode": "0"}}
- Reports mentioning engine failure: {"fn": "size", "filters": {"document": {"contains": "engine failure"}}, "distinct_by": "report_id"}
- Count by document type: {"fn": "size", "by": ["document_type"]}
- Unique reports by agency: {"fn": "nunique", "column": "report_id", "by": ["agency"]}
"""
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "search": ANALYZE_SEARCH_PARAMETERS,
            "fn": {
                "type": "string",
                "enum": [
                    "size",
                    "count",
                    "nunique",
                    "sum",
                    "mean",
                    "median",
                    "min",
                    "max",
                    "std",
                ],
                "description": "Aggregation function. Use size for row counts.",
            },
            "column": {
                "type": "string",
                "description": "Column to analyse. Required unless fn is size.",
            },
            "by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional group-by column(s). Omit for a single value.",
            },
            "filters": {
                "type": "object",
                "description": "Optional filters. Values may be scalars, lists, or operator objects, e.g. {'fatalities': {'>': 0}, 'location': {'contains': 'Wellington'}}.",
            },
            "distinct_by": {
                "type": "string",
                "description": "Optional column to deduplicate on before calculating, e.g. report_id.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum grouped rows. Default 50, max 200.",
            },
            "round": {
                "type": "integer",
                "description": "Round numeric results to this many decimal places (0-6).",
            },
        },
        "required": ["search", "fn"],
    }

    _ALLOWED_PARAMS: ClassVar[set[str]] = {
        "fn",
        "column",
        "by",
        "filters",
        "distinct_by",
        "limit",
        "round",
    }
    _AGGREGATIONS: ClassVar[set[str]] = {
        "size",
        "count",
        "nunique",
        "sum",
        "mean",
        "median",
        "min",
        "max",
        "std",
    }
    _COMPARISONS: ClassVar[dict[str, str]] = {
        "==": "eq",
        "!=": "ne",
        ">": "gt",
        ">=": "ge",
        "<": "lt",
        "<=": "le",
    }
    _MAX_LIMIT: ClassVar[int] = 200
    _MAX_ROUND: ClassVar[int] = 6

    def __init__(self, searcher: Searcher):  # noqa: D107
        self.searcher = searcher

    def execute(self, **kwargs) -> str:  # noqa: PLR0911
        """Run an embedded search and calculate statistics over its results.

        Returns:
            str: JSON-encoded result, or an error message.
        """
        username = kwargs.pop("username", None) or kwargs.pop("user", None)
        search = kwargs.pop("search", None)

        error = self._parameter_error(kwargs)
        if error is not None:
            logger.warning(
                "analyze_results rejected: user=%s params=%r error=%s",
                username or "<none>",
                kwargs,
                error,
            )
            return error

        if not isinstance(search, dict):
            return (
                "A `search` object is required. Pass the same search parameters "
                "you would use with the search tool."
            )

        search_args = dict(search)
        limit = search_args.pop("limit", 500)
        exclude_document = bool(search_args.pop("exclude_document", False))
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return "`search.limit` must be a positive integer."
        limit = min(limit, 5000)

        try:
            search_params, results, info = _execute_search(
                self.searcher,
                search_args,
                limit=limit,
                username=username,
            )
        except Exception as exc:
            logger.exception(
                "analyze_results search failed: user=%s search=%r",
                username or "<none>",
                search_args,
            )
            return f"Search failed: {type(exc).__name__}: {exc}"

        if results.empty:
            logger.info("analyze_results: empty search user=%s", username or "<none>")
            return "No search results matched the embedded search."

        if exclude_document and "document" in results.columns:
            results = results.drop(columns=["document"])

        logger.info(
            "analyze_results: user=%s fn=%r search=%r params=%r",
            username or "<none>",
            kwargs.get("fn"),
            search_args,
            kwargs,
        )
        try:
            result = self._stats(
                results,
                kwargs,
                document_excluded=exclude_document,
            )
        except AnalysisError as exc:
            logger.warning(
                "analyze_results rejected: user=%s params=%r error=%s",
                username or "<none>",
                kwargs,
                exc,
            )
            available = ", ".join(str(column) for column in results.columns)
            return f"Analysis error: {exc}\n\nAvailable columns: {available}"
        except Exception as exc:
            logger.exception(
                "analyze_results failed: user=%s params=%r",
                username or "<none>",
                kwargs,
            )
            return f"Analysis failed ({type(exc).__name__}): {exc}"

        result["search"] = {
            "query": search_params.query,
            "total_results": info.get("total_results", len(results)),
            "relevant_results": info.get("relevant_results", len(results)),
        }
        return json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )

    @classmethod
    def _parameter_error(cls, params: dict[str, Any]) -> str | None:
        if "expression" in params:
            return (
                "The `expression` parameter is no longer supported because it executed "
                "arbitrary Python. Pass fn, column, and optional by instead."
            )
        if "operation" in params or "op" in params:
            return (
                "Pass fn, column, by, filters, distinct_by, limit, and round directly; "
                "the `operation` wrapper was removed."
            )
        unexpected = sorted(set(params) - cls._ALLOWED_PARAMS)
        if unexpected:
            allowed = ", ".join(sorted(cls._ALLOWED_PARAMS))
            return f"Unknown parameter(s): {', '.join(unexpected)}. Allowed: {allowed}."
        return None

    def _stats(
        self,
        df: pd.DataFrame,
        params: dict[str, Any],
        *,
        document_excluded: bool,
    ) -> dict[str, Any]:
        fn = self._require_fn(params)
        by = params.get("by")
        by = [] if by is None else self._require_columns(df, by)
        if not by and "limit" in params:
            raise AnalysisError("`limit` only applies when `by` is provided.")

        filtered = self._dedupe(
            self._apply_filters(
                df,
                params.get("filters"),
                document_excluded=document_excluded,
            ),
            params.get("distinct_by"),
        )
        if fn == "size":
            if params.get("column") is not None:
                raise AnalysisError("`column` is not used when fn='size'.")
            column = None
        else:
            column = self._require_column(df, params.get("column"))

        try:
            if by:
                return self._group_result(filtered, by, fn, column, params)
            return self._scalar_result(filtered, fn, column, params)
        except AnalysisError:
            raise
        except (TypeError, ValueError, AttributeError) as exc:
            label = f" for column '{column}'" if column else ""
            raise AnalysisError(f"Calculation '{fn}'{label} failed: {exc}") from exc

    def _scalar_result(
        self,
        filtered: pd.DataFrame,
        fn: str,
        column: str | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        value = len(filtered) if fn == "size" else getattr(filtered[column], fn)()
        return {
            "fn": fn,
            "column": column,
            "value": self._jsonable(self._round(value, params.get("round"))),
            "matched_rows": len(filtered),
        }

    def _group_result(
        self,
        filtered: pd.DataFrame,
        by: list[str],
        fn: str,
        column: str | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if fn == "size":
            values = filtered.groupby(by, dropna=True).size()
        else:
            values = getattr(filtered.groupby(by, dropna=True)[column], fn)()

        limit = self._get_limit(params)
        table = values.reset_index(name="value")
        rows = [
            {str(key): self._jsonable(value) for key, value in record.items()}
            for record in table.head(limit).to_dict(orient="records")
        ]
        for row in rows:
            row["value"] = self._jsonable(
                self._round(row["value"], params.get("round")),
            )

        return {
            "by": by,
            "fn": fn,
            "column": column,
            "matched_rows": len(filtered),
            "groups": len(table),
            "truncated": len(table) > limit,
            "rows": rows,
        }

    def _apply_filters(
        self,
        df: pd.DataFrame,
        filters: Any,
        *,
        document_excluded: bool,
    ) -> pd.DataFrame:
        if filters is None:
            return df
        if not isinstance(filters, dict):
            raise AnalysisError("`filters` must be an object of column -> condition.")
        for column, condition in filters.items():
            if column == "document" and "document" not in df.columns:
                if document_excluded:
                    raise AnalysisError(
                        "**Cannot filter on `document` because the last search used "
                        "`exclude_document=true`.** Re-run the search with "
                        "`exclude_document=false`, then call analyze_results again.",
                    )
                raise AnalysisError(
                    "**Cannot filter on `document` because the last search results "
                    "do not contain the document text.** Re-run the search with "
                    "document text included, then call analyze_results again.",
                )
            column_name = self._require_column(df, column)
            if isinstance(condition, dict):
                if not condition:
                    raise AnalysisError(f"Filter for '{column}' is empty.")
                for operator, value in condition.items():
                    mask = self._filter_mask(
                        df[column_name],
                        column_name,
                        operator,
                        value,
                    )
                    df = df[mask]
            elif isinstance(condition, list):
                df = df[df[column_name].isin(condition)]
            else:
                df = df[df[column_name] == condition]
        return df

    def _filter_mask(
        self,
        series: pd.Series,
        column: str,
        operator: str,
        value: Any,
    ) -> pd.Series:
        comparison = self._COMPARISONS.get(operator)
        if comparison is not None:
            try:
                mask = getattr(series, comparison)(value)
            except TypeError as exc:
                raise AnalysisError(
                    f"Filter '{column} {operator} {value!r}' failed: {exc}",
                ) from exc
            return mask.fillna(value=False)

        if operator == "contains":
            if not isinstance(value, str):
                raise AnalysisError(
                    f"Filter op 'contains' requires a string value, got {type(value).__name__}.",
                )
            return (
                series.astype("string")
                .str.contains(value, case=False, na=False, regex=False)
                .fillna(value=False)
            )
        if operator in {"in", "not_in"}:
            if not isinstance(value, list):
                raise AnalysisError(
                    f"Filter op '{operator}' requires a list value.",
                )
            mask = series.isin(value)
            return ~mask if operator == "not_in" else mask

        raise AnalysisError(
            f"Unknown filter operator '{operator}'. "
            "Use ==, !=, >, >=, <, <=, in, not_in, or contains.",
        )

    def _dedupe(self, df: pd.DataFrame, column: Any) -> pd.DataFrame:
        if column is None:
            return df
        column = self._require_column(df, column)
        return df.drop_duplicates(subset=[column])

    def _require_fn(self, params: dict[str, Any]) -> str:
        if "fn" not in params:
            allowed = ", ".join(sorted(self._AGGREGATIONS))
            raise AnalysisError(f"`fn` is required. Use one of: {allowed}.")
        fn = params["fn"]
        if fn not in self._AGGREGATIONS:
            allowed = ", ".join(sorted(self._AGGREGATIONS))
            raise AnalysisError(f"Unknown aggregation '{fn}'. Use one of: {allowed}.")
        return fn

    @staticmethod
    def _require_column(df: pd.DataFrame, column: Any) -> str:
        if not isinstance(column, str) or not column:
            raise AnalysisError("`column` must be a non-empty string.")
        if column not in df.columns:
            available = ", ".join(str(name) for name in df.columns)
            raise AnalysisError(f"Unknown column '{column}'. Available columns: {available}")
        return column

    def _require_columns(self, df: pd.DataFrame, columns: Any) -> list[str]:
        if not isinstance(columns, list) or not columns:
            raise AnalysisError("`by` must be a non-empty list of column names.")
        return [self._require_column(df, column) for column in columns]

    def _get_limit(self, params: dict[str, Any]) -> int:
        value = params.get("limit", 50)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise AnalysisError("`limit` must be a positive integer.")
        if value > self._MAX_LIMIT:
            raise AnalysisError(f"`limit` cannot exceed {self._MAX_LIMIT}.")
        return value

    def _round(self, value: Any, digits: Any) -> Any:
        if digits is None:
            return value
        if (
            isinstance(digits, bool)
            or not isinstance(digits, int)
            or not 0 <= digits <= self._MAX_ROUND
        ):
            raise AnalysisError("`round` must be an integer between 0 and 6.")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise AnalysisError(
                f"`round` requires a numeric result, got {type(value).__name__}.",
            ) from exc
        return round(numeric, digits)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if value is None or value is pd.NA or value is pd.NaT:
            return None
        if isinstance(value, float) and pd.isna(value):
            return None
        if isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if hasattr(value, "item"):
            return AnalyzeResultsTool._jsonable(value.item())
        raise AnalysisError(
            f"Cannot serialize a result value of type {type(value).__name__}.",
        )


class FindRelevantReports(Tool):
    """Tool for bulk FTS search on the rpeort text and metadata filtering to find relevant report IDs and agency IDs for further analysis."""

    _tool_name = "search_report_text"
    _tool_description = """Search the report_text table (~4k rows) using FTS to find relevant report IDs and agency IDs.
This is useful for filtering: first search here to find which reports mention a topic, then pass the report_ids/agency_ids to the main search tool.

Use this with keyword-only searches (FTS) — it does not support vector/semantic search.

This is also very useful for filtering by metadata fields (e.g. aircraft type, engine type, occurrence type) to find relevant reports. As you can guarantee that each report is only listed once (unlike the main search tool which returns multiple rows per report), you can use this to get a concise list of reports for further analysis.

Returns a concise list of report IDs and agency IDs with their matching text excerpts.
"""
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "FTS keyword query to search in report full text.",
            },
            "year_range": {
                "type": "array",
                "description": f"Year range [start, end]. Valid range 2000-{datetime.now(tz=timezone.utc).year}.",
                "items": {"type": "number"},
            },
            "modes": {
                "type": "array",
                "description": "Filter by mode: 0=aviation, 1=rail, 2=marine.",
                "items": {"type": "string"},
            },
            "agencies": {
                "type": "array",
                "description": "Filter by agency: TAIC, ATSB, or TSB.",
                "items": {"type": "string"},
            },
            "location": {
                "type": "string",
                "description": "Filter by location text.",
            },
            "occurrence_type": {
                "type": "array",
                "description": "Filter by occurrence type.",
                "items": {"type": "string"},
            },
            "fatalities_range": {
                "type": "array",
                "description": "Filter by fatalities count range, e.g. [1, 10]. Use -1 for no upper bound.",
                "items": {"type": "number"},
            },
            "injuries_range": {
                "type": "array",
                "description": "Filter by injuries count range, e.g. [0, 5]. Use -1 for no upper bound.",
                "items": {"type": "number"},
            },
            "metadata_filter": {
                "type": "string",
                "description": "Filter metadata_json. 'key=value' for a specific field; plain text for full JSON search.",
            },
            "limit": {
                "type": "number",
                "description": "Maximum results to return. Default 250.",
            },
        },
        "required": ["query"],
    }

    def __init__(self, searcher: Searcher):  # noqa: D107
        self.searcher = searcher

    def execute(self, **kwargs) -> str:
        """Execute a FTS search on report_text table.

        Returns:
            str: Formatted list of matching report IDs, agency IDs, and text excerpts.
        """
        limit = kwargs.pop("limit", 250)
        username = kwargs.pop("username", None) or kwargs.pop("user", None)
        # Remove username from kwargs so it doesn't pollute SearchParams via get
        kwargs.pop("username", None)
        kwargs.pop("user", None)
        logger.info("search_report_text started: user=%s query=%r", username or "<none>", kwargs.get("query", ""))
        params = SearchParams(
            query=kwargs.get("query", ""),
            search_type="fts",
            year_range=kwargs.get(
                "year_range", (2000, datetime.now(tz=timezone.utc).year),
            ),
            document_type=["report_text"],
            modes=kwargs.get("modes", ["0", "1", "2"]),
            agencies=kwargs.get("agencies", ["TAIC", "ATSB", "TSB"]),
            location=kwargs.get("location"),
            occurrence_type=kwargs.get("occurrence_type", []),
            fatalities_range=kwargs.get("fatalities_range"),
            injuries_range=kwargs.get("injuries_range"),
            metadata_filter=kwargs.get("metadata_filter"),
        )

        results, _info = self.searcher.knowledge_search_report_text(params, limit=limit, username=username)

        if results.empty:
            logger.info("search_report_text completed: user=%s query=%r rows=0", username or "<none>", kwargs.get("query", ""))
            return "No matching reports found in report_text table."

        summary = results[
            ["report_id", "agency_id", "agency", "year"]
        ].drop_duplicates()
        logger.info(
            "search_report_text completed: user=%s query=%r rows=%d reports=%d",
            username or "<none>",
            kwargs.get("query", ""),
            len(results),
            len(summary),
        )
        lines = [f"Found {len(results)} matches across {len(summary)} unique reports."]
        lines.append("")
        for _, row in summary.iterrows():
            lines.append(
                f"- report_id={row['report_id']}, agency_id={row['agency_id']}, agency={row['agency']}, year={row['year']}",
            )
        return "\n".join(lines)


class ReadReportTool(Tool):
    """Tool for reading the full text of a report by report ID."""

    _tool_name = "read_report"
    _tool_description = "Read the full text of a report from the report_text table (~4k rows). Provide report_id or agency_id."
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "report_id": {
                "type": "string",
                "description": "The report ID from search results (e.g. 'ATSB_a_2000_648').",
            },
            "agency_id": {
                "type": "string",
                "description": "The agency's own ID (e.g. 'AO-2000-003' for TAIC, '200002648' for ATSB).",
            },
        },
    }

    def __init__(self, searcher: Searcher):
        """Constructor.

        Parameters:
            searcher: Searcher instance with access to the report_text table.
        """
        self.searcher = searcher

    def execute(self, **kwargs) -> str:
        """Execute a report lookup.

        Returns:
            str: The full text of the report, or a not-found message.
        """
        username = kwargs.pop("username", None) or kwargs.pop("user", None)
        report_id = kwargs.get("report_id", "")
        agency_id = kwargs.get("agency_id", "")

        logger.info("read_report started: user=%s report_id=%r agency_id=%r", username or "<none>", report_id, agency_id)

        if not report_id and not agency_id:
            logger.warning("read_report missing identifier user=%s", username or "<none>")
            return "Either report_id or agency_id must be provided."

        result = self.searcher.read_report(
            report_id=report_id or None,
            agency_id=agency_id or None,
            username=username,
        )

        if result is None:
            identifier = report_id or agency_id
            return f"No report found with '{identifier}'."

        return result


class SkillsTool(Tool):
    """Tool for reading the full content of an investigator skill."""

    _tool_name = "read_skill"
    _tool_description = """Read the full content of a specific investigator skill for detailed guidance.
Call with a skill name (e.g. 'finding-previous-occurrences') to get the complete instructions."""

    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Name of the skill to read (e.g. 'finding-previous-occurrences').",
            },
        },
        "required": ["skill_name"],
    }

    _skills_dir: ClassVar[Path] = Path(__file__).parent.parent / "skills"
    _skill_name_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )

    @classmethod
    def get_available_skills_markdown(cls) -> str:
        """Return a markdown bullet list of all available skills with descriptions.

        Reads the YAML frontmatter from each .md file in the skills directory.
        Returns an empty string if no skills directory or no skill files exist.
        """
        if not cls._skills_dir.exists():
            return ""

        skill_files = sorted(cls._skills_dir.glob("*.md"))
        if not skill_files:
            return ""

        lines: list[str] = []
        for skill_file in skill_files:
            content = skill_file.read_text(encoding="utf-8")
            name = skill_file.stem
            description = "No description available."
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:  # noqa: PLR2004
                    for line in parts[1].strip().split("\n"):
                        line = line.strip()  # noqa: PLW2901
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip('"')
            lines.append(f"- **{name}** — {description}")
        return "\n".join(lines)

    def __init__(self):  # noqa: D107
        pass

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter from markdown content."""  # noqa: DOC201
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:  # noqa: PLR2004
                return parts[2].strip()
        return content

    @classmethod
    def _resolve_skill_path(cls, skill_name: str) -> Path | None:
        """Resolve a skill name to a file inside the skills directory.

        Args:
            skill_name: User/model-supplied skill name.

        Returns:
            Path | None: Safe resolved path, or None if invalid/outside.
        """
        if not isinstance(skill_name, str):
            return None

        skill_name = skill_name.strip()
        if not cls._skill_name_pattern.fullmatch(skill_name):
            return None

        skills_dir = cls._skills_dir.resolve()
        candidate = cls._skills_dir / f"{skill_name}.md"
        if candidate.is_symlink():
            return None

        resolved = candidate.resolve()
        try:
            resolved.relative_to(skills_dir)
        except ValueError:
            return None

        return resolved if resolved.is_file() else None

    @classmethod
    def _find_by_frontmatter(cls, skill_name: str) -> str | None:
        """Find a skill by its frontmatter name inside the skills directory.

        Args:
            skill_name: Validated skill name.

        Returns:
            str | None: Skill body, or None when no safe match exists.
        """
        skills_dir = cls._skills_dir.resolve()
        for skill_file in sorted(cls._skills_dir.glob("*.md")):
            if skill_file.is_symlink():
                continue
            resolved = skill_file.resolve()
            try:
                resolved.relative_to(skills_dir)
            except ValueError:
                continue
            if not resolved.is_file():
                continue

            content = resolved.read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            parts = content.split("---", 2)
            if len(parts) < 3:  # noqa: PLR2004
                continue
            if any(
                line.strip() == f"name: {skill_name}"
                for line in parts[1].strip().split("\n")
            ):
                body = cls._strip_frontmatter(content)
                return body or content
        return None

    @classmethod
    def execute(cls, **kwargs) -> str:
        """Execute the skills tool.

        Returns:
            str: Full skill content.
        """
        skill_name = kwargs.get("skill_name", "")
        if not skill_name:
            return "Please provide a skill_name parameter."
        if not isinstance(skill_name, str) or not cls._skill_name_pattern.fullmatch(
            skill_name.strip(),
        ):
            return "Invalid skill name. Use a name like 'finding-previous-occurrences'."

        skill_name = skill_name.strip()
        skill_path = cls._resolve_skill_path(skill_name)
        if skill_path is None:
            content = cls._find_by_frontmatter(skill_name)
            if content is not None:
                return content
            return f"Skill '{skill_name}' not found."

        content = skill_path.read_text(encoding="utf-8")
        body = cls._strip_frontmatter(content)
        return body or content


class DelegateTool(Tool):
    """Tool for delegating tool execution + analysis to a separate LLM call.

    Use this when you have large amounts of data that would overflow your context
    window. You provide one or more tool calls to execute, their results are sent
    to a fresh AI instance that only sees that data plus your instructions, and
    returns a concise summary/analysis.

    Tool calls run in parallel when possible.
    """

    _tool_name = "delegate_analysis"
    _tool_description = """Execute one or more tools and analyse their combined output with a separate AI.
Useful when you have too much data to fit in your context window. You specify tool calls
to run (e.g. search_tool, read_report) plus analysis instructions. The tools are executed,
and their results are sent to a fresh AI that only sees that content.

Examples:
- delegate_analysis(tool_calls=[{"name": "search_tool", "arguments": {"query": "engine failure helicopter", "limit": 50}}], instruction="Summarise the common safety themes")
- delegate_analysis(tool_calls=[{"name": "read_report", "arguments": {"report_id": "..."}}], instruction="List all safety issues")
- Multiple: delegate_analysis(tool_calls=[{"name": "search_tool", "arguments": {...}}, {"name": "search_tool", "arguments": {...}}], instruction="Compare and contrast findings")
"""
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tool_calls": {
                "type": "array",
                "description": "List of tool calls to execute. Each has 'name' (tool name) and 'arguments' (dict of params). Results are collected and analysed together.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Tool name to call (e.g. search_tool, read_report, find_relevant_reports).",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Arguments to pass to the tool.",
                        },
                    },
                    "required": ["name", "arguments"],
                },
                "minItems": 1,
            },
            "instruction": {
                "type": "string",
                "description": "Instructions for the analysis. Be specific about what you want extracted from the combined results.",
            },
        },
        "required": ["tool_calls", "instruction"],
    }

    def __init__(self, client, tool_map, model):  # noqa: D107
        self.client = client
        self.tool_map = tool_map
        self.model = model

    def execute(self, **kwargs) -> str:
        """Execute tool calls and send results to a fresh LLM for analysis.

        Returns:
            str: The analysis result from the delegated AI call.
        """
        tool_calls = kwargs.get("tool_calls", [])
        instruction = kwargs.get("instruction", "")

        if not tool_calls or not instruction:
            return "Both tool_calls and instruction are required."

        results = [None] * len(tool_calls)

        def run_tool(i, tc):
            tool_name = tc.get("name", "")
            tool_args = dict(tc.get("arguments", {}))
            tool = self.tool_map.get(tool_name)
            if not tool:
                output = f"Error: Unknown tool '{tool_name}'"
            else:
                try:
                    output = tool.execute(**tool_args)
                except Exception as e:  # noqa: BLE001
                    output = f"Error executing {tool_name}: {e}"
            return i, tool_name, tool_args, output

        with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
            futures = [executor.submit(run_tool, i, tc) for i, tc in enumerate(tool_calls)]
            for future in as_completed(futures):
                i, tool_name, tool_args, output = future.result()
                results[i] = f"## Tool: {tool_name}\nArguments: {json.dumps(tool_args, default=str)}\n\nOutput:\n{output}"

        combined_data = "\n\n---\n\n".join(results)

        system_prompt = """You are an expert accident investigator and analyst. You will be given the combined output of one or more tool calls and asked to analyse it.
Provide a concise, well-structured analysis. Focus on extracting key findings, patterns, and insights."""

        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=f"## Tool Results\n\n{combined_data}\n\n## Instructions\n\n{instruction}",
            store=False,
        )

        return response.output_text
