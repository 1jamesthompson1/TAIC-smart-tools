"""Tests for the Searching module."""

import os
import time

import lancedb
import pandas as pd
import pytest

from backend.Searching import Searcher, SearchParams


class TestSearching:
    """Tests for the Searching functionality."""

    # As a side note this would be a good position (or maybe in the Engine code base) to add in the evaluation set that we should to know how well it find the information that we are interested in.

    DEFAULT_SEARCH_LIMIT = 10

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @staticmethod
    def test_real_searcher_initialization(mock_searcher: Searcher):
        """Test that the real searcher initializes properly."""
        assert mock_searcher is not None
        assert hasattr(mock_searcher, "knowledge_search")
        assert hasattr(mock_searcher, "db_version")
        assert hasattr(mock_searcher, "last_updated")

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @pytest.mark.parametrize("search_type", ["fts", "vector"])
    def test_basic_knowledge_search(self, mock_searcher: Searcher, search_type: str):
        """Test basic knowledge search functionality."""
        params = SearchParams(
            query="fatigue",
            search_type=search_type,
            modes=["0", "1", "2"],
            agencies=["TAIC"],
            document_type=["safety_issue"],
            year_range=(2010, 2024),
        )

        results, info, _plots = mock_searcher.knowledge_search(
            params=params,
            limit=self.DEFAULT_SEARCH_LIMIT,
        )

        assert isinstance(results, pd.DataFrame)
        assert isinstance(info, dict)
        assert "total_results" in info
        assert len(results) <= self.DEFAULT_SEARCH_LIMIT

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @pytest.mark.parametrize("search_type", ["fts", "vector"])
    def test_empty_search(self, mock_searcher: Searcher, search_type: str):
        """Test search that has no search query and simply a filter search."""
        params = SearchParams(
            query="",
            search_type=search_type,
            modes=["0", "1", "2"],
            agencies=["TAIC"],
            document_type=["safety_issue"],
            year_range=(2007, 2010),
        )
        results, info, _plots = mock_searcher.knowledge_search(
            params=params,
            limit=self.DEFAULT_SEARCH_LIMIT,
        )

        assert isinstance(results, pd.DataFrame)
        assert isinstance(info, dict)

        assert len(results) == self.DEFAULT_SEARCH_LIMIT

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @pytest.mark.parametrize(
        "doc_type",
        ["safety_issue", "recommendation", "report_section"],
    )
    def test_search_with_year_filtering(self, mock_searcher: Searcher, doc_type: str):
        """Test search with year range filtering."""
        # Test different year ranges
        year_ranges = [(2007, 2015), (2015, 2024)]

        for year_range in year_ranges:
            params = SearchParams(
                query="",
                search_type="fts",
                modes=["0", "1", "2"],
                agencies=["TAIC", "ATSB", "TSB"],
                document_type=[doc_type],
                year_range=year_range,
            )
            results, info, _plots = mock_searcher.knowledge_search(
                params=params,
                limit=self.DEFAULT_SEARCH_LIMIT,
            )

            assert isinstance(results, pd.DataFrame)
            assert isinstance(info, dict)

            # Make sure that all results fall within the year range
            if not results.empty and "year" in results.columns:
                assert results["year"].between(year_range[0], year_range[1]).all()

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @pytest.mark.parametrize("relevance", [0.1, 0.5, 0.8])
    def test_search_relevance_filtering(
        self,
        mock_searcher: Searcher,
        relevance: float,
    ):
        """Test search with different relevance thresholds."""
        params = SearchParams(
            query="safety factor",
            search_type="vector",
            modes=["0", "1", "2"],
            agencies=["TAIC"],
            document_type=["safety_issue"],
            year_range=(2010, 2024),
        )

        results, info, _plots = mock_searcher.knowledge_search(
            params=params,
            relevance=relevance,
            limit=self.DEFAULT_SEARCH_LIMIT,
        )

        assert isinstance(results, pd.DataFrame)
        assert isinstance(info, dict)

        assert "relevant_results" in info
        assert info["relevant_results"] <= info["total_results"]

        # Make sure that relevance is meaning results are no less than the limit
        assert (results["relevance"] >= relevance).all()

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    def test_empty_search_results(self, mock_searcher: Searcher):
        """Test handling of searches that return no results."""
        params = SearchParams(
            query="nonexistentterm12345",
            document_type=["safety_issue"],
            year_range=(2007, 2010),
            modes=["0", "1", "2"],
            agencies=["TAIC"],
            search_type="fts",
        )
        results, info, _plots = mock_searcher.knowledge_search(
            params=params,
            limit=self.DEFAULT_SEARCH_LIMIT,
        )

        assert isinstance(results, pd.DataFrame)
        assert isinstance(info, dict)
        assert info["total_results"] == 0

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    def test_search_performance(self, mock_searcher):
        """Test that searches complete within reasonable time."""
        params = SearchParams(
            query="safety factor",
            search_type="vector",
            modes=["0", "1", "2"],
            agencies=["TAIC"],
            document_type=["safety_issue"],
            year_range=(2010, 2024),
        )

        start_time = time.time()
        _results, _info, _plots = mock_searcher.knowledge_search(
            params=params,
            limit=self.DEFAULT_SEARCH_LIMIT * 5,
        )
        end_time = time.time()

        runtime = end_time - start_time
        expected_runtime = 60  # seconds; remote Azure vector DBs are slower
        assert runtime < expected_runtime, f"Search took too long: {runtime} seconds"

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    def test_large_result_set(self, mock_searcher):
        """Test handling of larger result sets."""
        params = SearchParams(
            query="fatigue",
            search_type="vector",
            modes=["0", "1", "2"],
            agencies=["TAIC", "ATSB", "TSB"],
            document_type=["safety_issue", "recommendation"],
            year_range=(2007, 2024),
        )

        results, info, _plots = mock_searcher.knowledge_search(
            params=params,
            limit=self.DEFAULT_SEARCH_LIMIT * 100,
        )

        assert isinstance(results, pd.DataFrame)
        assert len(results) <= self.DEFAULT_SEARCH_LIMIT * 100
        assert isinstance(info, dict)


class TestWhereStatement:
    """Regression tests for LanceDB WHERE clause generation."""

    @staticmethod
    def _build_where(**overrides) -> str:
        """Build a WHERE clause with the filters from the failing production query.

        Returns:
            str: The generated LanceDB WHERE clause.
        """
        params = {
            "year_range": (2020, 2025),
            "document_type": [],
            "modes": [],
            "agencies": [],
        }
        params.update(overrides)
        return Searcher._Searcher__get_where_statement(**params)  # noqa: SLF001

    @staticmethod
    def _make_table(tmp_path):
        """Create a small local LanceDB table for filter execution tests.

        Returns:
            lancedb.table.Table: Table containing one row per document type.
        """
        db = lancedb.connect(tmp_path / "where-test")
        return db.create_table(
            "documents",
            data=[
                {
                    "year": 2021,
                    "document_type": "safety_issue",
                    "mode": "0",
                    "agency": "TAIC",
                    "occurrence_type": "collision",
                },
                {
                    "year": 2022,
                    "document_type": "recommendation",
                    "mode": "1",
                    "agency": "ATSB",
                    "occurrence_type": "fire",
                },
                {
                    "year": 2024,
                    "document_type": "summary",
                    "mode": "0",
                    "agency": "TAIC",
                    "occurrence_type": "pilot's error",
                },
            ],
        )

    def test_document_type_values_execute_as_string_literals(self, tmp_path):
        """Double-quoted values are parsed as columns by LanceDB/DataFusion."""
        where = self._build_where(
            document_type=["safety_issue", "recommendation"],
            modes=["0"],
            agencies=["TAIC", "ATSB"],
        )
        assert "document_type in ('safety_issue', 'recommendation')" in where
        assert '"safety_issue"' not in where

        table = self._make_table(tmp_path)
        results = table.search(None).where(where, prefilter=True).limit(10).to_pandas()

        assert results["document_type"].tolist() == ["safety_issue"]

    def test_single_quotes_in_values_are_escaped(self, tmp_path):
        """Values containing apostrophes remain valid SQL literals."""
        where = self._build_where(occurrence_type=["pilot's error"])
        assert "occurrence_type = 'pilot''s error'" in where

        table = self._make_table(tmp_path)
        results = table.search(None).where(where, prefilter=True).limit(10).to_pandas()

        assert results["occurrence_type"].tolist() == ["pilot's error"]
