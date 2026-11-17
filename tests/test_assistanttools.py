"""Tests for the AssistantTools module."""

import json
import os
from unittest.mock import Mock

import pandas as pd
import pytest

from backend.AssistantTools import AnalyzeResultsTool, SearchTool, SkillsTool

SEARCH = {
    "query": "",
    "year_range": [2000, 2026],
    "document_type": ["section"],
    "modes": ["0"],
    "agencies": ["TAIC"],
}


@pytest.fixture
def analysis_df() -> pd.DataFrame:
    """Build a small search-results frame for analysis tests.

    Returns:
        pd.DataFrame: Search-like rows with varied columns.
    """
    return pd.DataFrame(
        {
            "document": [
                "engine failure",
                "fire",
                "engine failure",
                "collision",
                "fire",
            ],
            "document_id": ["d1", "d2", "d3", "d4", "d5"],
            "report_id": ["R1", "R2", "R3", "R4", "R5"],
            "agency_id": ["A1", "A1", "A2", "A3", "A3"],
            "year": [2020, 2021, 2020, 2022, 2022],
            "mode": ["0", "0", "1", "2", "2"],
            "agency": ["TAIC", "TAIC", "ATSB", "TSB", "TSB"],
            "document_type": [
                "safety_issue",
                "recommendation",
                "summary",
                "section",
                "summary",
            ],
            "location": ["A", "B", "C", "D", "E"],
            "occurrence_type": ["type1", "type2", "type3", "type4", "type5"],
            "fatalities": [0, 1, 2, 0, 3],
            "injuries": [1, 0, 2, 0, 1],
        },
    )


@pytest.fixture
def analyze_tool(analysis_df: pd.DataFrame) -> AnalyzeResultsTool:
    """Build an AnalyzeResultsTool whose embedded search returns fixture rows.

    Returns:
        AnalyzeResultsTool: Tool backed by a mocked searcher.
    """
    searcher = Mock()
    searcher.knowledge_search.return_value = (
        analysis_df,
        {"total_results": len(analysis_df), "relevant_results": len(analysis_df)},
        {},
    )
    return AnalyzeResultsTool(searcher)


def _run(tool: AnalyzeResultsTool, params: dict) -> dict:
    """Execute a statistics call and decode the JSON result.

    Returns:
        dict: Decoded analysis payload.
    """
    result = tool.execute(search=SEARCH, **params)
    return json.loads(result)


def test_analyze_groupby_document_type(analyze_tool: AnalyzeResultsTool):
    """Count rows by document type."""
    result = _run(
        analyze_tool,
        {"fn": "size", "by": ["document_type"]},
    )

    assert result["matched_rows"] == 5  # noqa: PLR2004
    counts = {row["document_type"]: row["value"] for row in result["rows"]}
    assert counts == {
        "safety_issue": 1,
        "recommendation": 1,
        "summary": 2,
        "section": 1,
    }
    assert result["groups"] == 4  # noqa: PLR2004


def test_analyze_filter_unique_reports(analyze_tool: AnalyzeResultsTool):
    """Filter by agency and count unique reports."""
    result = _run(
        analyze_tool,
        {
            "fn": "nunique",
            "column": "report_id",
            "filters": {"agency": "TAIC"},
        },
    )

    assert result["value"] == 2  # noqa: PLR2004
    assert result["matched_rows"] == 2  # noqa: PLR2004


def test_analyze_groupby_year_size(analyze_tool: AnalyzeResultsTool):
    """Group by year and count rows."""
    result = _run(
        analyze_tool,
        {"fn": "size", "by": ["year"]},
    )

    assert {row["year"]: row["value"] for row in result["rows"]} == {
        2020: 2,
        2021: 1,
        2022: 2,
    }


def test_analyze_mean_fatalities(analyze_tool: AnalyzeResultsTool):
    """Calculate the average fatalities."""
    result = _run(
        analyze_tool,
        {"fn": "mean", "column": "fatalities"},
    )

    assert result["value"] == pytest.approx(1.2)
    assert result["matched_rows"] == 5  # noqa: PLR2004


def test_analyze_groupby_agency_nunique(analyze_tool: AnalyzeResultsTool):
    """Group by agency and count unique reports."""
    result = _run(
        analyze_tool,
        {
            "by": ["agency"],
            "fn": "nunique",
            "column": "report_id",
        },
    )

    assert {row["agency"]: row["value"] for row in result["rows"]} == {
        "TAIC": 2,
        "ATSB": 1,
        "TSB": 2,
    }


def test_analyze_groupby_mean_round(analyze_tool: AnalyzeResultsTool):
    """Group by mode, average fatalities, and round the result."""
    result = _run(
        analyze_tool,
        {
            "by": ["mode"],
            "fn": "mean",
            "column": "fatalities",
            "round": 1,
        },
    )

    assert {row["mode"]: row["value"] for row in result["rows"]} == {
        "0": 0.5,
        "1": 2.0,
        "2": 1.5,
    }


def test_analyze_mean_fatalities_with_filter(analyze_tool: AnalyzeResultsTool):
    """Average fatalities for aviation only."""
    result = _run(
        analyze_tool,
        {
            "fn": "mean",
            "column": "fatalities",
            "filters": {"mode": "0"},
        },
    )

    assert result["value"] == pytest.approx(0.5)
    assert result["matched_rows"] == 2  # noqa: PLR2004


def test_analyze_fatal_accidents(analyze_tool: AnalyzeResultsTool):
    """Average fatalities among fatal accidents only."""
    result = _run(
        analyze_tool,
        {
            "fn": "mean",
            "column": "fatalities",
            "filters": {"fatalities": {">": 0}},
            "distinct_by": "report_id",
        },
    )

    assert result["value"] == pytest.approx(2.0)
    assert result["matched_rows"] == 3  # noqa: PLR2004


def test_analyze_numeric_range_filter(analyze_tool: AnalyzeResultsTool):
    """Combine two numeric comparisons on one column."""
    result = _run(
        analyze_tool,
        {
            "fn": "mean",
            "column": "fatalities",
            "filters": {"fatalities": {">=": 1, "<": 3}},
        },
    )

    assert result["value"] == pytest.approx(1.5)
    assert result["matched_rows"] == 2  # noqa: PLR2004


def test_analyze_text_contains(analyze_tool: AnalyzeResultsTool):
    """Count reports whose document text contains a phrase."""
    result = _run(
        analyze_tool,
        {
            "fn": "size",
            "filters": {"document": {"contains": "ENGINE"}},
            "distinct_by": "report_id",
        },
    )

    assert result["value"] == 2  # noqa: PLR2004
    assert result["matched_rows"] == 2  # noqa: PLR2004


def test_analyze_deduplicates_reports():
    """Average fatalities counts each report once."""
    frame = pd.DataFrame(
        {
            "report_id": ["R1", "R1", "R2"],
            "fatalities": [2, 2, 0],
        },
    )
    searcher = Mock()
    searcher.knowledge_search.return_value = (frame, {"total_results": 3}, {})
    tool = AnalyzeResultsTool(searcher)

    result = _run(
        tool,
        {
            "fn": "mean",
            "column": "fatalities",
            "distinct_by": "report_id",
        },
    )

    assert result["value"] == pytest.approx(1.0)
    assert result["matched_rows"] == 2  # noqa: PLR2004


def test_analyze_document_excluded_warns():
    """Filtering on an excluded document column gives a loud message."""
    frame = pd.DataFrame({"report_id": ["R1"], "document": ["text"]})
    searcher = Mock()
    searcher.knowledge_search.return_value = (frame, {"total_results": 1}, {})
    tool = AnalyzeResultsTool(searcher)

    result = tool.execute(
        search={**SEARCH, "exclude_document": True},
        fn="size",
        filters={"document": {"contains": "engine"}},
    )

    assert "Cannot filter on `document`" in result
    assert "exclude_document=true" in result


def test_analyze_runs_embedded_search(analyze_tool: AnalyzeResultsTool):
    """Analysis performs its own search instead of reading global state."""
    _run(analyze_tool, {"fn": "size"})

    analyze_tool.searcher.knowledge_search.assert_called_once()
    assert not hasattr(analyze_tool, "_last_results")


def test_analyze_rejects_unknown_parameter(analyze_tool: AnalyzeResultsTool):
    """Reject typos instead of silently ignoring them."""
    result = analyze_tool.execute(
        search=SEARCH,
        fn="mean",
        column="fatalities",
        filter={"agency": "TAIC"},
    )

    assert "Unknown parameter(s): filter" in result


def test_analyze_limit_requires_grouping(analyze_tool: AnalyzeResultsTool):
    """Reject limit when there is no group-by instead of ignoring it."""
    result = analyze_tool.execute(
        search=SEARCH, fn="mean", column="fatalities", limit=10,
    )

    assert "`limit` only applies when `by` is provided" in result


def test_analyze_rejects_column_with_size(analyze_tool: AnalyzeResultsTool):
    """Reject a column when fn=size instead of silently ignoring it."""
    result = analyze_tool.execute(search=SEARCH, fn="size", column="report_id")

    assert "`column` is not used when fn='size'" in result


def test_analyze_rejects_excessive_limit(analyze_tool: AnalyzeResultsTool):
    """Reject limit values above the cap instead of silently reducing them."""
    result = analyze_tool.execute(
        search=SEARCH, fn="size", by=["agency"], limit=500,
    )

    assert "`limit` cannot exceed 200" in result


def test_analyze_rejects_unknown_column(analyze_tool: AnalyzeResultsTool):
    """Reject column names that are not part of the search results."""
    result = analyze_tool.execute(
        search=SEARCH,
        fn="mean",
        column="__import__('os')",
    )

    assert "Analysis error" in result
    assert "Unknown column" in result


def test_analyze_rejects_legacy_expression(analyze_tool: AnalyzeResultsTool):
    """The old arbitrary-Python expression parameter is rejected."""
    result = analyze_tool.execute(expression="__import__('os').system('true')")

    assert "no longer supported" in result


def test_read_skill_confined_to_skills_dir(tmp_path, monkeypatch):
    """read_skill reads valid skills and rejects path traversal."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "good.md").write_text(
        "---\nname: good\n---\nGOOD CONTENT",
        encoding="utf-8",
    )
    monkeypatch.setattr(SkillsTool, "_skills_dir", skills_dir)

    assert SkillsTool.execute(skill_name="good") == "GOOD CONTENT"

    for skill_name in ("../outside", "/etc/passwd", "good.md"):
        result = SkillsTool.execute(skill_name=skill_name)
        assert result.startswith("Invalid skill name.")


@pytest.mark.skipif(
    not os.getenv("TEST_USE_REAL_SERVICES"),
    reason="Requires real services",
)
def test_tool_execution_search(mock_searcher):
    """Test that search tool actually executes."""
    search_tool = SearchTool(mock_searcher)
    result = search_tool.execute(
        query="safety factor",
        document_type=["safety_issue"],
        year_range=(2010, 2024),
        modes=["0", "1", "2"],
        agencies=["TAIC"],
        search_type="vector",
    )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "table" in result.lower()  # Should return a html table
