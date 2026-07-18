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
    _tool_description = """Search for safety issues and recommendations from the New Zealand Transport Accident Investigation Commission. This function searches a vector database.
Example usage:
search(query=\"What are the common causes of aviation accidents?\", search_type=\"vector\", year_range=[2010, 2023], document_type=[\"safety_issue\", \"recommendation\"], modes=[\"0\"], agencies=[\"TAIC\"])

search(query=\"What safety issues are associated with runway incursions?\", search_type=\"vector\", year_range=[2000, 2023], document_type=[\"safety_issue\", \"recommendation\"], modes=[\"0\"], agencies=[\"TAIC\", \"ATSB\"])

search(query=\"What are some recent accidents?\", search_type=\"vector\", year_range=[2000, 2023], document_type=[\"summary\"], modes=[\"0\", \"1\", \"2\"], agencies=[\"ATSB\", \"TSB\", \"TAIC\"])
"""
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The query to search for. If left as an empty string it will return all results that match the other parameters.",
            },
            "search_type": {
                "type": "string",
                "enum": ["fts", "vector"],
                "description": "The type of search to perform. fts should be used if the query is asking a specific question about an organisation, etc. Otherwise for more general information use vector, it will embed your query and search the vector database.",
            },
            "year_range": {
                "type": "array",
                "description": f"An array specifying the start and end years for filtering results. Valid range is 2000-{datetime.now(tz=timezone.utc).year}.",
                "items": {"type": "number"},
            },
            "document_type": {
                "type": "array",
                "description": "A list of document types to filter the search results. Safety issues and recommendations follow definitions given, while report sections are reports chunked into sections/pages, summary are brief overviews of the reports scrapped from the agencies report webpages only available for TAIC and ATSB. Valid types are 'safety_issue', 'recommendation', 'section' and 'summary'.",
                "items": {"type": "string"},
            },
            "modes": {
                "type": "array",
                "description": "A list of modes to filter the search results. Valid modes are 0, 1, and 2. Which are aviation, rail, and marine respectively.",
                "items": {"type": "string"},
            },
            "agencies": {
                "type": "array",
                "description": "A list of agencies to filter the search results. Valid agencies are TSB, ATSB, and TAIC. These are Transport Safety Board (Canada), Australian Transport Safety Board, and Transport Accident Investigation Commission (New Zealand) respectively.",
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
    _tool_description = """Retrieve the full text of a transport accident investigation report by its report ID. Note the report ID is not the same as the agency ID and is TAIC engine specific.
Use this when the user wants to read an entire report or see full details beyond what search snippets provide.
The report ID is typically in the format like "ATSB_a_2000_648" or "TAIC_m_2002_201".

Example usage:
read_report(report_id=\"ATSB_a_2000_648\")
"""
    _tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "report_id": {
                "type": "string",
                "description": "The report ID to retrieve (e.g. 'ATSB_a_2000_648', 'TAIC_m_2002_201'). This is the report_id field from search results.",
            },
            "agency_id": {
                "type": "string",
                "description": "The agency's own ID for the report (e.g. 'AO-2000-003' for TAIC, '200002648' for ATSB). This is the agency_id field from search results.",
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
