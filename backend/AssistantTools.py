"""Tools that the AI assistant can use to search, read reports, and reason."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

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
Use query="" (empty string) for filter-only searches.
Document types:

- safety_issue — AI extraction from report text + website scraping for ATSB post-2008. All agencies.
  TAIC: confident extraction. TSB: inferred from "findings as to risk". ATSB post-2008: scraped; pre-2008: best-effort.
- recommendation — website scraping for TSB/TAIC; AI extraction for ATSB. All agencies.
  TSB/TAIC: scraped from websites. ATSB: AI extracted (confident); context/recipient/made fields are best-effort.
- section — AI extraction. All agencies. Report text chunked by page/section from parsed PDF.
- summary — website scraping. TAIC and ATSB only. Brief overviews from agency report webpages.

Examples:
search(query="What are the common causes of aviation accidents?", search_type="vector", year_range=[2010, 2023], document_type=["safety_issue", "recommendation"], modes=["0"], agencies=["TAIC"])
search(query="", search_type="vector", year_range=[2020, 2023], modes=["0", "1"], agencies=["ATSB"])
search(query="What safety issues are associated with runway incursions?", search_type="vector", year_range=[2000, 2023], document_type=["safety_issue", "recommendation"], modes=["0"], agencies=["TAIC", "ATSB"])
"""
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query. Empty string returns all results matching other filters.",
            },
            "search_type": {
                "type": "string",
                "enum": ["fts", "vector"],
                "description": "fts for specific questions (organisation names etc), vector for general semantic similarity.",
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
                "description": "Filter by fatalities count range, e.g. [1, 10] for 1 to 10 fatalities.",
                "items": {"type": "number"},
            },
            "injuries_range": {
                "type": "array",
                "description": "Filter by injuries count range, e.g. [0, 5] for 0 to 5 injuries.",
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
        },
        "required": [
            "query",
            "search_type",
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
            str: HTML formatted search results.
        """
        results, info, _plots = self.searcher.knowledge_search(
            SearchParams(**kwargs),
        )

        results_html = results.to_html(index=False)

        return f"<p>Information about the search:<br>{info}<br>Search Results:<br></p>{results_html}"


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
