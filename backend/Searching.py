"""Module enabling semantic search and embedding.

This module is used by the semantic search tool.
"""

import contextvars
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import ClassVar, Literal, NamedTuple

# Context var so embedding logs can include user even though LanceDB calls
# generate_embeddings without explicit user param.
_current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_user", default=None)

import lancedb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
from azure.ai.inference import EmbeddingsClient  # noqa: E402
from azure.core.credentials import AzureKeyCredential  # noqa: E402
from lancedb.embeddings.base import TextEmbeddingFunction  # noqa: E402
from lancedb.embeddings.registry import register  # noqa: E402
from lancedb.embeddings.utils import TEXT  # noqa: E402
from rich import print as rich_print  # noqa: E402
from rich import table  # noqa: E402

logger = logging.getLogger(__name__)


# This has to be added in manually unless https://github.com/lancedb/lancedb/issues/2518 is resolved
@register("azure-ai-text")
class AzureAITextEmbeddingFunction(TextEmbeddingFunction):
    """An embedding function that uses the AzureAI API.

    https://learn.microsoft.com/en-us/python/api/overview/azure/ai-inference-readme?view=azure-python-preview

    - AZURE_AI_ENDPOINT: The endpoint URL for the AzureAI service.
    - AZURE_AI_API_KEY: The API key for the AzureAI service.

    Attributes:
        name: The name of the model you want to use from the model catalog.

    Examples:
        Usage example:

        ```python
        import lancedb
        import pandas as pd
        from lancedb.pydantic import LanceModel, Vector
        from lancedb.embeddings import get_registry

        model = get_registry().get("azure-ai-text").create(name="embed-v-4-0")

        class TextModel(LanceModel):
            text: str = model.SourceField()
            vector: Vector(model.ndims()) = model.VectorField()

        df = pd.DataFrame({"text": ["hello world", "goodbye world"]})
        db = lancedb.connect("lance_example")
        tbl = db.create_table("test", schema=TextModel, mode="overwrite")

        tbl.add(df)
        rs = tbl.search("hello").limit(1).to_pandas()
        #           text                                             vector  _distance
        # 0  hello world  [-0.018188477, 0.0134887695, -0.013000488, 0.0...   0.841431
        ```
    """

    name: str
    client: ClassVar = None

    def ndims(self) -> int:
        """Return the number of dimensions used.

        Checks the embedding model used and returns the number of dimensions.

        Returns:
            int: Number of dimensions for the embedding model.

        Raises:
            ValueError: If unknown model.
        """
        if self.name == "embed-v-4-0":
            return 1536
        if self.name in {"Cohere-embed-v3-english", "Cohere-embed-v3-multilingual"}:
            return 1024
        if self.name == "text-embedding-ada-002":
            return 1536
        if self.name == "text-embedding-3-large":
            return 3072
        if self.name == "text-embedding-3-small":
            return 1536
        msg = f"Unknown model name: {self.name}"
        raise ValueError(msg)

    def compute_query_embeddings(self, query: str, *_args, **_kwargs) -> list[np.array]:
        """Calculate embedding for given query string.

        Wrapper for compute_source_embeddings.

        Parameters:
            query: Text to search.

        Returns:
            Embedding of the query parameter.
        """
        return self.compute_source_embeddings(query, input_type="query")

    def compute_source_embeddings(
        self,
        texts: TEXT,
        *_args,
        **kwargs,
    ) -> list[np.array]:
        """Calculate embedding for given texts parameter.

        Sanitize texts and return the embedding of the texts parameter.
        Wrapper for generate_embeddings.

        Parameters:
            texts: The texts to embed.

        Returns:
            Embedding of the texts parameter.
        """
        texts = self.sanitize_input(texts)
        input_type = (
            kwargs.get("input_type") or "document"
        )  # assume source input type if not passed by `compute_query_embeddings`
        return self.generate_embeddings(texts, input_type=input_type)

    def generate_embeddings(  # noqa: PLR0914
        self,
        texts: list[str] | np.ndarray,
        *_args,
        **kwargs,
    ) -> list[np.array]:
        """Get the embeddings for the given texts.

        Parameters:
            texts: The texts to embed.

        Returns:
            list: Embedding

        Raises:
            ValueError: If texts parameter is an np.ndarray with the wrong data type.
            TimeoutError: If embedding request exceeds timeout.
        """
        AzureAITextEmbeddingFunction._init_client()

        if isinstance(texts, np.ndarray):
            if texts.dtype != object:
                msg = (
                    "AzureAITextEmbeddingFunction only supports input of strings for numpy \
                        arrays."
                )
                raise ValueError(
                    msg,
                )
            texts = texts.tolist()

        # batch process so that no more than 96 texts are sent at once.
        batch_size = 96
        embeddings = []
        total_batches = (len(texts) + batch_size - 1) // batch_size
        embed_timeout = int(os.getenv("EMBED_TIMEOUT_SECONDS", "30"))
        input_type = kwargs.get("input_type", "document")
        active_user = _current_user.get()
        user_suffix = f" user={active_user}" if active_user else ""
        overall_start = time.perf_counter()
        logger.info(
            "Generating embeddings: model=%s input_type=%s texts=%d batches=%d timeout=%ds%s",
            self.name,
            input_type,
            len(texts),
            total_batches,
            embed_timeout,
            user_suffix,
        )
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_idx = i // batch_size + 1
            batch_start = time.perf_counter()
            try:
                # Run embed in a thread with timeout so a hanging Azure call
                # doesn't block the whole search forever.
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        AzureAITextEmbeddingFunction.client.embed,
                        input=batch,
                        model=self.name,
                        dimensions=self.ndims(),
                        **kwargs,
                    )
                    rs = future.result(timeout=embed_timeout)
            except FutureTimeoutError:
                elapsed = time.perf_counter() - batch_start
                logger.exception("Embedding batch %d/%d TIMEOUT after %.2fs (timeout=%ds model=%s%s). "
                    "Azure endpoint: %s — check AZURE_AI_ENDPOINT / network / API key.",
                    batch_idx,
                    total_batches,
                    elapsed,
                    embed_timeout,
                    self.name,
                    user_suffix,
                    os.getenv("AZURE_AI_ENDPOINT", "<not set>"),
                    )
                msg = (
                    f"Embedding request timed out after {embed_timeout}s "
                    f"(batch {batch_idx}/{total_batches}, model={self.name}). "
                    "This usually means the Azure AI endpoint is unreachable or slow."
                )
                raise TimeoutError(msg) from None
            except Exception:
                elapsed = time.perf_counter() - batch_start
                logger.exception("Embedding batch %d/%d FAILED after %.2fs: model=%s%s",
                    batch_idx,
                    total_batches,
                    elapsed,
                    self.name,
                    user_suffix,
                    )
                raise
            else:
                embeddings.extend(emb.embedding for emb in rs.data)
        overall_elapsed = time.perf_counter() - overall_start
        logger.info(
            "Embedding generation completed in %.2fs: total=%d batches=%d model=%s%s",
            overall_elapsed,
            len(embeddings),
            total_batches,
            self.name,
            user_suffix,
        )
        return embeddings

    @staticmethod
    def _init_client():
        if AzureAITextEmbeddingFunction.client is None:
            endpoint = os.getenv("AZURE_AI_ENDPOINT")
            api_key_present = bool(os.getenv("AZURE_AI_API_KEY"))
            logger.debug(
                "Initializing AzureAI EmbeddingsClient: endpoint=%s api_key_present=%s model_init_pending",
                endpoint or "<not set>",
                api_key_present,
            )
            if os.environ.get("AZURE_AI_API_KEY") is None:
                logger.exception("AZURE_AI_API_KEY not found in environment variables")  # noqa: LOG004
                msg = "AZURE_AI_API_KEY not found in environment variables"
                raise ValueError(msg)
            if os.environ.get("AZURE_AI_ENDPOINT") is None:
                logger.error("AZURE_AI_ENDPOINT not found in environment variables")
                msg = "AZURE_AI_ENDPOINT not found in environment variables"
                raise ValueError(msg)

            try:
                AzureAITextEmbeddingFunction.client = EmbeddingsClient(
                    endpoint=os.environ["AZURE_AI_ENDPOINT"],
                    credential=AzureKeyCredential(os.environ["AZURE_AI_API_KEY"]),
                )
                logger.debug(
                    "AzureAI EmbeddingsClient initialized successfully: endpoint=%s",
                    os.environ["AZURE_AI_ENDPOINT"],
                )
            except Exception:
                logger.error("Failed to initialize AzureAI EmbeddingsClient")  # noqa: TRY400
                raise


class SearchParams(NamedTuple):
    """Simple class to store search parameters.

    Attributes:
        query (str): Query
        search_type: (str): Type of search (fts or vector)
        year_range (tuple[int, int]): Year range for the search
        document_type (list[str]): Document type
        modes (list[str]): Modes to include
        agencies (list[str]): Investigation agencies to include
        location (str | None): Location text to filter by.
        occurrence_type (list[str]): Occurrence/event types to include.
        fatalities_range (tuple[int, int] | None): Fatalities count range.
        injuries_range (tuple[int, int] | None): Injuries count range.
    """

    query: str
    search_type: Literal["fts", "vector"] | None
    year_range: tuple[int, int]
    document_type: list[str]
    modes: list[str]
    agencies: list[str]
    location: str | None = None
    occurrence_type: list[str] = []  # noqa: RUF012
    fatalities_range: tuple[int, int] | None = None
    injuries_range: tuple[int, int] | None = None
    metadata_filter: str | None = None
    report_ids: list[str] = []  # noqa: RUF012
    agency_ids: list[str] = []  # noqa: RUF012


class Searcher:
    """Manage knowledge search functionality."""

    def __init__(self, db_uri: str, table_name: str):
        """Constructor.

        Parameters:
            db_uri: URI of the database to search.
            table_name: Name of the table to search.

        Raises:
            ValueError: If fails to open table.
        """
        logger.info("Creating Searcher: db_uri=%s table=%s", db_uri, table_name)
        self.vector_db = lancedb.connect(db_uri)
        logger.info("LanceDB connected: uri=%s tables=%s", db_uri, self.vector_db.table_names())
        try:
            self.all_document_types_table = self.vector_db.open_table(table_name)
            logger.info("Opened table '%s' successfully", table_name)
        except ValueError as e:
            logger.exception("Error opening table '%s': %s. Available tables: %s",
                table_name,
                e,  # noqa: TRY401
                self.vector_db.table_names(),
                )
            raise

        try:
            self.report_text_table = self.vector_db.open_table("report_text")
            logger.info("Opened report_text table")
        except ValueError:
            logger.warning("report_text table not found — report_text search disabled")
            self.report_text_table = None

        self.last_updated = self.all_document_types_table.list_versions()[-1][
            "timestamp"
        ].strftime("%Y-%m-%d")
        self.db_version = self.all_document_types_table.version
        # Keep rich table for interactive CLI, but also log structured info
        logger.info(
            "Searcher config: uri=%s table=%s version=%s last_updated=%s rows=%d columns=%s",
            db_uri,
            table_name,
            self.all_document_types_table.version,
            self.last_updated,
            self.all_document_types_table.count_rows(),
            ", ".join(self.all_document_types_table.schema.names),
        )
        searcher_config = table.Table(title="🔍 Searcher Config", show_header=True)
        searcher_config.add_column("Name")
        searcher_config.add_column("Value")
        searcher_config.add_row("Database URI", db_uri)
        searcher_config.add_row("Table Name", table_name)
        searcher_config.add_row(
            "Table Version",
            str(self.all_document_types_table.version),
        )
        searcher_config.add_row("Last updated", self.last_updated)
        searcher_config.add_row(
            "Table Size",
            f"{self.all_document_types_table.count_rows()} rows",
        )
        searcher_config.add_row(
            "Columns",
            ", ".join(self.all_document_types_table.schema.names),
        )
        rich_print(searcher_config)

        if "agency" not in self.all_document_types_table.schema.names:
            logger.exception("agency column not found in table '%s' schema: %s", table_name, self.all_document_types_table.schema.names)  # noqa: LOG004
            msg = "agency column not found in table"
            raise ValueError(msg)

    @staticmethod
    def __get_where_statement(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917
        year_range: tuple[int, int],
        document_type: list[str],
        modes: list[str],
        agencies: list[str],
        location: str | None = None,
        occurrence_type: list[str] | None = None,
        fatalities_range: tuple[int, int] | None = None,
        injuries_range: tuple[int, int] | None = None,
        metadata_filter: str | None = None,
        report_ids: list[str] | None = None,
        agency_ids: list[str] | None = None,
    ) -> str:
        r"""Generate the where statement of the query.

        Parameters:
            year_range: Year range for the search
            document_type: Document types to include
            modes: Modes to include
            agencies: Investigation agencies to include
            location: Location text filter
            occurrence_type: Occurrence/event types to include
            fatalities_range: Fatalities count range
            injuries_range: Injuries count range
            metadata_filter: Dot-path or free-text filter on metadata_json column
                (e.g. \"aircraft.0.type_of_engines=piston\" or \"Helicopter\")
            report_ids: Filter by specific report IDs (e.g. [\"ATSB_a_2000_648\"])
            agency_ids: Filter by specific agency IDs (e.g. [\"AO-2000-003\", \"200002648\"])

        Returns:
            str: Where clause as text.
        """
        where_statement = []
        if year_range:
            where_statement.append(
                f"year >= {int(year_range[0])} and year <= {int(year_range[1])}",
            )
        if document_type:
            document_types = ", ".join(f'"{dt}"' for dt in document_type)
            where_statement.append(f"document_type in ({document_types})")
        if modes and len(modes) > 1:
            where_statement.append(f"mode in {tuple(str(mode) for mode in modes)}")
        elif modes and len(modes) == 1:
            where_statement.append(f"mode = '{modes[0]!s}'")
        if agencies and len(agencies) > 1:
            where_statement.append(f"agency in {tuple(agencies)}")
        elif agencies and len(agencies) == 1:
            where_statement.append(f"agency = '{agencies[0]}'")
        if location:
            where_statement.append(f"location like '%{location}%'")
        if occurrence_type and len(occurrence_type) > 1:
            ot_list = ", ".join(f'"{ot}"' for ot in occurrence_type)
            where_statement.append(f"occurrence_type in ({ot_list})")
        elif occurrence_type and len(occurrence_type) == 1:
            where_statement.append(f"occurrence_type = '{occurrence_type[0]}'")
        if fatalities_range:
            if int(fatalities_range[1]) == -1:
                where_statement.append(
                    f"fatalities >= {int(fatalities_range[0])}",
                )
            else:
                where_statement.append(
                    f"fatalities >= {int(fatalities_range[0])} and fatalities <= {int(fatalities_range[1])}",
                )
        if injuries_range:
            if int(injuries_range[1]) == -1:
                where_statement.append(
                    f"injuries >= {int(injuries_range[0])}",
                )
            else:
                where_statement.append(
                    f"injuries >= {int(injuries_range[0])} and injuries <= {int(injuries_range[1])}",
                )
        if metadata_filter:
            if "=" in metadata_filter:
                key_path, value = metadata_filter.split("=", 1)
                key_name = key_path.strip().rsplit(".", 1)[-1]
                safe_value = value.strip().replace("'", "''")
                where_statement.append(
                    f'metadata_json like \'%"{key_name}"%: "{safe_value}"%\'',
                )
            else:
                safe_value = metadata_filter.strip().replace("'", "''")
                where_statement.append(
                    f"metadata_json like '%{safe_value}%'",
                )
        if report_ids and len(report_ids) > 1:
            rid_list = ", ".join(f"'{r}'" for r in report_ids)
            where_statement.append(f"report_id in ({rid_list})")
        elif report_ids and len(report_ids) == 1:
            where_statement.append(f"report_id = '{report_ids[0]}'")
        if agency_ids and len(agency_ids) > 1:
            aid_list = ", ".join(f"'{a}'" for a in agency_ids)
            where_statement.append(f"agency_id in ({aid_list})")
        elif agency_ids and len(agency_ids) == 1:
            where_statement.append(f"agency_id = '{agency_ids[0]}'")

        return " AND ".join(where_statement)

    @staticmethod
    def __print_search_query(
        query: str,
        final_query: str | list[float] | None,
        where_statement: str,
    ):
        """Log and print search query.

        Parameters:
            query (str): Query text.
            final_query (str | list[float] | None): Final query as str or vector.
            where_statement (str): Where clause as text.

        """
        # Structured log for production
        query_preview = (
            final_query if isinstance(final_query, str) else f"vector({query[:80]!r})" if final_query else "None"
        )
        logger.debug(
            "Search query: query=%r final_query=%r filters=%r",
            query,
            query_preview,
            where_statement or "<none>",
        )
        # Keep rich table for local dev visibility
        query_table = table.Table(
            title="🔍 Conducting search with 🔍",
            show_header=True,
            title_style="bold blue",
        )
        query_table.add_column("Parameter")
        query_table.add_column("Value")
        if final_query is None:
            query_table.add_row("Query", "None")
        else:
            query_table.add_row(
                "Query",
                final_query
                if isinstance(final_query, str)
                else "vector embeddings of " + query,
            )
        if where_statement:
            query_table.add_row("Filters", where_statement)
        if logger.isEnabledFor(logging.DEBUG):
            rich_print(query_table)

    def knowledge_search(  # noqa: C901, PLR0912
        self,
        params: SearchParams,
        limit: int = 150,
        relevance: float = 0,
        username: str | None = None,
    ) -> tuple[pd.DataFrame, dict, list | None]:
        """Run query.

        Parameters:
            params (SearchParams): Search parameters.
            limit (int): Maximum number of results to return. Defaults to 150.
            relevance (float): Relevance criteria. Defaults to 0.
            username (str | None): Active user for log correlation.

        Returns:
            results (DataFrame): Results as a Pandas DataFrame.
            info (dict): Additional info.
            plots: Plots to display.

        Raises:
            ValueError: If incorrect search type.

        """
        token = _current_user.set(username) if username else None
        search_start = time.perf_counter()
        logger.info("knowledge_search called: user=%s query=%r search_type=%r limit=%d", username or "<none>", params.query, params.search_type, limit)
        info: dict = {"info_message": ""}
        if "TSB" in params.agencies and "summary" in params.document_type:
            info["info_message"] += "Summaries are only available for ATSB and TAIC reports, not TSB reports.\n"

        where_statement = self.__get_where_statement(
            year_range=params.year_range,
            document_type=params.document_type,
            modes=params.modes,
            agencies=params.agencies,
            location=params.location,
            occurrence_type=params.occurrence_type,
            fatalities_range=params.fatalities_range,
            injuries_range=params.injuries_range,
            metadata_filter=params.metadata_filter,
            report_ids=params.report_ids,
            agency_ids=params.agency_ids,
        )

        final_query: list[float] | str | None = None
        if not params.query or params.query is None:
            final_query = None
            params = params._replace(search_type=None)
        elif params.search_type in {"fts", "vector"}:
            final_query = params.query
        else:
            if token:
                _current_user.reset(token)
            msg = f"type must be 'fts' or 'vector' not {params.search_type}"
            raise ValueError(msg)

        try:  # noqa: PLW0717
            search = self.all_document_types_table.search(
                final_query, query_type=params.search_type)
            if params.search_type == "vector":
                search = search.metric("cosine")
            results = (
                search.where(where_statement, prefilter=True).limit(limit).to_pandas()
            ).drop(columns=["vector"])

            if final_query is not None:
                if "_distance" in results.columns:
                    results["_distance"] = 1 - results["_distance"]
                results = results.rename(columns={
                    "_relevance_score": "relevance", "_score": "relevance", "_distance": "relevance"})
                results = results.sort_values(by=["relevance"], ascending=False).reset_index(drop=True)
                cols = ["relevance"] + [c for c in results.columns if c != "relevance"]
                results = results[cols]
                if relevance > 0:
                    results = results[results["relevance"] >= relevance]

            info["total_results"] = len(results)
            info["relevant_results"] = len(results)

            results["mode"] = results["mode"].apply(
                lambda x: {"0": "aviation", "1": "rail", "2": "maritime"}.get(str(x).strip(), str(x)))

            plots = {
                "document_type": GraphMaker(results).get_document_type_pie_chart(),
                "mode": GraphMaker(results).get_mode_pie_chart(),
                "year": GraphMaker(results).get_year_histogram(),
                "event_type": GraphMaker(results).get_most_common_event_types(),
                "agency": GraphMaker(results).get_agency_pie_chart(),
            }

        except Exception:
            logger.exception("knowledge_search failed user=%s query=%r", username or "<none>", params.query)
            if token:
                _current_user.reset(token)
            raise

        total_elapsed = time.perf_counter() - search_start
        info["search_duration_seconds"] = round(total_elapsed, 2)
        logger.info(
            "knowledge_search results: query=%r total=%d relevant=%d elapsed=%.2fs user=%s",
            params.query, info["total_results"], info["relevant_results"], total_elapsed, username or "<none>",
        )
        if total_elapsed > 20:  # noqa: PLR2004
            logger.warning("Slow knowledge_search: %.2fs user=%s query=%r", total_elapsed, username or "<none>", params.query)

        if token:
            _current_user.reset(token)
        return results, info, plots

    def knowledge_search_report_text(
        self,
        params: SearchParams,
        limit: int = 50,
        username: str | None = None,
    ) -> tuple[pd.DataFrame, dict]:
        """Search the report_text table using FTS.

        Parameters:
            params: Search parameters.
            limit: Maximum number of results. Defaults to 50.
            username (str | None): Active user for log correlation.

        Returns:
            results: Results as a Pandas DataFrame.
            info: Additional info dict.

        Raises:
            ValueError: If report_text table is not available.
        """
        if self.report_text_table is None:
            logger.exception("report_text table not available for user=%s query=%r", username or "<none>", params.query)  # noqa: LOG004
            msg = "report_text table is not available"
            raise ValueError(msg)

        where_statement = self.__get_where_statement(
            year_range=params.year_range,
            document_type=params.document_type,
            modes=params.modes,
            agencies=params.agencies,
            location=params.location,
            occurrence_type=params.occurrence_type,
            fatalities_range=params.fatalities_range,
            injuries_range=params.injuries_range,
            metadata_filter=params.metadata_filter,
            report_ids=params.report_ids,
            agency_ids=params.agency_ids,
        )

        self.__print_search_query(params.query, params.query, where_statement)
        search_start = time.perf_counter()
        logger.debug(
            "report_text search started: query=%r limit=%d filters=%r user=%s",
            params.query,
            limit,
            where_statement or "<none>",
            username or "<none>",
        )

        try:
            search = self.report_text_table.search(
                params.query,
                query_type="fts",
            )

            if where_statement:
                results = search.where(where_statement, prefilter=True).limit(limit).to_pandas()
            else:
                results = search.limit(limit).to_pandas()
        except Exception:
            logger.error(  # noqa: TRY400
                "report_text search FAILED: query=%r limit=%d filters=%r user=%s",
                params.query,
                limit,
                where_statement,
                username or "<none>",
                )
            raise

        if "_score" in results.columns:
            results = results.rename(columns={"_score": "relevance"})
            results = results.sort_values(by=["relevance"], ascending=False)
            results = results.reset_index(drop=True)

        elapsed = time.perf_counter() - search_start
        logger.debug(
            "report_text search completed in %.2fs: query=%r rows=%d user=%s",
            elapsed,
            params.query,
            len(results),
            username or "<none>",
        )
        if elapsed > 10:  # noqa: PLR2004
            logger.warning("Slow report_text search: %.2fs query=%r rows=%d user=%s", elapsed, params.query, len(results), username or "<none>")

        info = {"total_results": len(results)}

        return results, info

    def read_report(self, report_id: str | None = None, agency_id: str | None = None, username: str | None = None) -> str | None:
        r"""Retrieve the full text of a report from the report_text table.

        Parameters:
            report_id: The report ID to look up (e.g. \"ATSB_a_2000_648\").
            agency_id: The agency's own ID to look up (e.g. \"AO-2000-003\").
            username (str | None): Active user for log correlation.

        Returns:
            The full document text, or None if not found.
        """  # noqa: DOC501
        if self.report_text_table is None:
            logger.exception("read_report table not available for user=%s", username or "<none>")  # noqa: LOG004
            msg = "report_text table is not available"
            raise ValueError(msg)

        if not report_id and not agency_id:
            logger.warning("read_report missing identifier for user=%s", username or "<none>")
            msg = "Either report_id or agency_id must be provided"
            raise ValueError(msg)

        if report_id:
            filter_expr = f"report_id = '{report_id}'"
            identifier = report_id
        else:
            filter_expr = f"agency_id = '{agency_id}'"
            identifier = agency_id

        try:
            results = (
                self.report_text_table.search()
                .where(filter_expr)
                .limit(1)
                .to_pandas()
            )
        except Exception:
            logger.error("read_report FAILED for %s=%r user=%s", "report_id" if report_id else "agency_id", identifier, username or "<none>")  # noqa: TRY400
            raise

        if results.empty:
            logger.warning("No report found with %r user=%s", identifier, username or "<none>")
            return None

        row = results.iloc[0]
        found_id = row.get("report_id", identifier)
        logger.info("Found report %s with %d characters (requested %r) user=%s", found_id, len(row["document"]), identifier, username or "<none>")
        return row["document"]


class GraphMaker:
    """Supports the display of graphs summaries of search queries."""

    def __init__(self, context):
        """Constructor."""
        self.context = context

    @staticmethod
    def add_visual_layout(fig: go.Figure) -> go.Figure:
        """Plot a graph.

        Returns:
            The generated graph.
        """
        fig = fig.update_layout(width=310)

        # If fig a pie chart
        if fig.data[0].type == "pie":
            fig.update_traces(
                textposition="inside",
                textinfo="percent+label",
                insidetextorientation="radial",
            )

            # Remove legend
            fig.update_layout(showlegend=False)

        return fig

    def get_document_type_pie_chart(self) -> go.Figure:
        """Plot pie chart for 'document type'.

        Returns:
            A pie chart graph.
        """
        context_df = self.context["document_type"].value_counts().reset_index()
        context_df.columns = ["document_type", "count"]
        fig = px.pie(
            context_df,
            values="count",
            names="document_type",
            title="Document type distribution",
        )

        return self.add_visual_layout(fig)

    def get_mode_pie_chart(self) -> go.Figure:
        """Plot pie chart for 'mode'.

        Returns:
            A pie chart graph.
        """
        context_df = self.context["mode"].value_counts().reset_index()
        context_df.columns = ["mode", "count"]
        fig = px.pie(
            context_df,
            values="count",
            names="mode",
            title="Mode distribution",
        )

        return self.add_visual_layout(fig)

    def get_year_histogram(self) -> go.Figure:
        """Plot histogram per 'year'.

        Returns:
            An histogram.
        """
        context_df = self.context
        fig = px.histogram(
            context_df,
            x="year",
            title="Year distributions",
        )
        return self.add_visual_layout(fig)

    def get_most_common_event_types(self) -> go.Figure:
        """Plot pie chart for most common event types.

        Returns:
            A pie chart graph.
        """
        context_df = self.context
        type_counts = context_df.groupby("occurrence_type")["document"].count()

        top_5_types = type_counts.nlargest(5).reset_index()

        others_count = type_counts[~type_counts.index.isin(top_5_types["occurrence_type"])].sum()

        combined_df = pd.concat(
            [
                top_5_types,
                pd.DataFrame([["Others", others_count]], columns=["occurrence_type", "document"]),
            ],
            ignore_index=True,
        )

        combined_df.columns = ["Event type", "Count"]

        fig = px.pie(
            combined_df,
            values="Count",
            names="Event type",
            title="Top 5 most common event types",
        )

        return self.add_visual_layout(fig)

    def get_agency_pie_chart(self) -> go.Figure:
        """Plot pie chart for 'agency'.

        Returns:
            A pie chart graph.
        """
        context_df = self.context["agency"].value_counts().reset_index()
        context_df.columns = ["agency", "count"]
        fig = px.pie(
            context_df,
            values="count",
            names="agency",
            title="Agency distribution",
        )

        return self.add_visual_layout(fig)
