"""Tools that the AI assistant can use to search, read reports, and reason."""

import json
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

from .Searching import Searcher, SearchParams


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

    def __init__(
        self, searcher: Searcher, analyze_tool: "AnalyzeResultsTool | None" = None
    ):
        """Constructor."""
        self.searcher = searcher
        self.analyze_tool = analyze_tool

    def execute(self, **kwargs) -> str:
        """Execute a search against the knowledge base.

        Returns:
            str: Markdown formatted search results.
        """
        limit = kwargs.pop("limit", 150)

        exclude_document = kwargs.pop("exclude_document", False)

        # Ensure search_type has a default for filter-only searches
        if "search_type" not in kwargs or kwargs["search_type"] is None:
            kwargs.setdefault("search_type", None)

        search_params = SearchParams(**kwargs)
        results, info, _plots = self.searcher.knowledge_search(
            search_params,
            limit=limit,
        )

        if self.analyze_tool is not None:
            self.analyze_tool.update_results(results)

        summary = f"**Search results:** {info.get('relevant_results', len(results))} relevant out of {len(results)} total"
        if info.get("info_message"):
            summary += f"\n_{info['info_message']}_"
        if exclude_document:
            summary += "\n_document column excluded (exclude_document=true) — reduced context overhead_"

        # Make agency_id clickable if URL present, only if columns exist
        if "agency_id" in results.columns and "url" in results.columns:
            results["agency_id"] = results.apply(
                lambda row: (
                    row["agency_id"]
                    if pd.isna(row["url"])
                    else f"<a href='{row['url']}'>{row['agency_id']}</a>"
                ),
                axis=1,
            )

        # Always drop internal / redundant columns if present
        results = results.drop(
            columns=[c for c in ["url", "year", "agency", "report_id"] if c in results.columns]
        )

        # Optionally drop the large document column to save context
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


class AnalyzeResultsTool(Tool):
    """Tool for running pandas queries on the last search results for quantitative analysis."""

    _tool_name = "analyze_results"
    _tool_description = """Run simple pandas queries on the last search results to get quantitative insights.
Use column names from the search results: document, document_id, report_id, agency_id, year, mode, agency, document_type, location, occurrence_type, fatalities, injuries, relevance.

Examples:
# Count by document type
df['document_type'].value_counts().to_dict()

# Filter and count
df[df['agency'] == 'TAIC']['report_id'].nunique()

# Group by year
df.groupby('year').size().to_dict()

# Average fatalities
df['fatalities'].mean()

# Count unique reports
df['report_id'].nunique()
"""
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "A pandas expression using 'df' as the last search results DataFrame. Returns str/repr of the result.",
            },
        },
        "required": ["expression"],
    }

    def __init__(self, searcher: Searcher):
        self.searcher = searcher
        self._last_results: pd.DataFrame | None = None

    def update_results(self, results: pd.DataFrame | None):
        """Store the latest search results for analysis."""
        self._last_results = results

    def execute(self, **kwargs) -> str:
        """Execute a pandas expression against the last search results.

        Returns:
            str: The result of the expression as a string.
        """
        if self._last_results is None or self._last_results.empty:
            return "No search results available. Run a search first."

        expression = kwargs.get("expression", "")
        if not expression:
            return "No expression provided."

        try:
            df = self._last_results
            result = eval(expression, {"df": df, "pd": pd})
            return str(result)
        except Exception as e:
            return f"Error evaluating expression: {e}\n\nAvailable columns: {list(self._last_results.columns)}"


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

    def __init__(self, searcher: Searcher):
        self.searcher = searcher

    def execute(self, **kwargs) -> str:
        """Execute a FTS search on report_text table.

        Returns:
            str: Formatted list of matching report IDs, agency IDs, and text excerpts.
        """
        limit = kwargs.pop("limit", 250)
        params = SearchParams(
            query=kwargs.get("query", ""),
            search_type="fts",
            year_range=kwargs.get(
                "year_range", (2000, datetime.now(tz=timezone.utc).year)
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

        results, info = self.searcher.knowledge_search_report_text(params, limit=limit)

        if results.empty:
            return "No matching reports found in report_text table."

        summary = results[
            ["report_id", "agency_id", "agency", "year"]
        ].drop_duplicates()
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
        report_id = kwargs.get("report_id", "")
        agency_id = kwargs.get("agency_id", "")

        if not report_id and not agency_id:
            return "Either report_id or agency_id must be provided."

        result = self.searcher.read_report(
            report_id=report_id or None,
            agency_id=agency_id or None,
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
                if len(parts) >= 3:
                    for line in parts[1].strip().split("\n"):
                        line = line.strip()
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip('"')
            lines.append(f"- **{name}** — {description}")
        return "\n".join(lines)

    def __init__(self):
        pass

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return content

    @classmethod
    def execute(cls, **kwargs) -> str:
        """Execute the skills tool.

        Returns:
            str: Full skill content.
        """
        skill_name = kwargs.get("skill_name", "")
        if not skill_name:
            return "Please provide a skill_name parameter."

        skill_path = cls._skills_dir / f"{skill_name}.md"
        if not skill_path.exists():
            # Try matching by frontmatter name
            for skill_file in cls._skills_dir.glob("*.md"):
                content = skill_file.read_text(encoding="utf-8")
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3 and f"name: {skill_name}" in parts[1]:
                        body = cls._strip_frontmatter(content)
                        return body if body else content

            return f"Skill '{skill_name}' not found."

        content = skill_path.read_text(encoding="utf-8")
        body = cls._strip_frontmatter(content)
        return body if body else content


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

    def __init__(self, client, tool_map):
        self.client = client
        self.tool_map = tool_map

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
            tool_args = tc.get("arguments", {})
            tool = self.tool_map.get(tool_name)
            if not tool:
                output = f"Error: Unknown tool '{tool_name}'"
            else:
                try:
                    output = tool.execute(**tool_args)
                except Exception as e:
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
            model="gpt-5.6-luna",
            instructions=system_prompt,
            input=f"## Tool Results\n\n{combined_data}\n\n## Instructions\n\n{instruction}",
            store=False,
        )

        return response.output_text
