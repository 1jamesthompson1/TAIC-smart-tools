"""Tests for the AssistantTools module."""

import os

import pytest

from backend.AssistantTools import SearchTool


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
