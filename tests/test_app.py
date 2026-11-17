"""Tests for the main application module."""

import contextlib
import os
import uuid
from io import StringIO
from unittest.mock import Mock, patch

import gradio as gr
import pandas as pd
import pytest
from azure.identity import DefaultAzureCredential

from app import (
    assistant_instance,
    conversation_store,
    create_or_update_conversation,
    delete_conversation,
    get_user_conversations_metadata,
    handle_example_select,
    load_conversation,
    perform_search,
    validate_runtime_config,
)
from backend import Storage
from backend.Assistant import CompleteHistory


class TestAppSmoke:
    """Smoke tests for the web application."""
    SUCCESS_STATUS = 200

    @staticmethod
    def test_app_imports_without_error():
        """Test that the app module can be imported without errors."""
        try:
            import app  # noqa: PLC0415

            assert app.app is not None
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"Failed to import app: {e}")

    def test_login_page_loads(self, client):
        """Test that login page loads successfully."""
        response = client.get("/login-page")
        assert response.status_code == self.SUCCESS_STATUS
        assert "TAIC smart tools" in response.text

    @staticmethod
    def test_root_redirect_without_auth(client):
        """Test that root path redirects to login when not authenticated."""
        response = client.get("/")
        assert "Please login to continue" in response.text

    @staticmethod
    def test_tool_redirect_without_auth(client):
        """Test that /tools path redirects to login when not authenticated."""
        response = client.get("/tools")
        assert "Please login to continue" in response.text

    @staticmethod
    def test_tool_with_auth(client):
        """Test that /tools path loads when authenticated."""
        with patch("app.get_user") as mock_get_user:
            mock_get_user.return_value = "testuser"
            response = client.get("/tools")

            assert "Assistant" in response.text

    @staticmethod
    def test_dev_banner_visible(client):
        """Test that DEV=true is visible in the UI."""
        with patch("app.get_user") as mock_get_user:
            mock_get_user.return_value = "testuser"
            response = client.get("/tools")

            assert "DEV MODE" in response.text

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    def test_real_app_startup(self, client):
        """Test that the real app starts up with all services initialized."""
        # Test that we can access the login page
        response = client.get("/login-page")
        assert response.status_code == self.SUCCESS_STATUS
        assert "TAIC smart tools" in response.text


class TestSecurityConfig:
    """Tests for the DEV switch and storage authentication."""

    @staticmethod
    def test_dev_required_for_dev_switches(monkeypatch):
        """NO_LOGIN and NO_LOGS must fail closed unless DEV=true."""
        monkeypatch.delenv("DEV", raising=False)
        monkeypatch.setenv("NO_LOGIN", "true")
        monkeypatch.setenv("NO_LOGS", "true")

        with pytest.raises(RuntimeError, match="DEV=true"):
            validate_runtime_config()

    @staticmethod
    def test_dev_allows_dev_switches(monkeypatch):
        """DEV=true permits NO_LOGIN and NO_LOGS for local development."""
        monkeypatch.setenv("DEV", "true")
        monkeypatch.setenv("NO_LOGIN", "true")
        monkeypatch.setenv("NO_LOGS", "true")

        validate_runtime_config()

    @staticmethod
    def test_storage_uses_entra_credential():
        """Storage authentication is Microsoft Entra / managed identity only."""
        assert isinstance(Storage.get_storage_credential(), DefaultAzureCredential)


class TestToolsFunctions:
    """Tests for the core tool functions (search, assistant integration)."""

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @staticmethod
    def test_real_assistant_integration():
        """Test that the assistant is properly integrated."""
        assert assistant_instance is not None
        assert assistant_instance.searcher is not None
        assert len(assistant_instance.tools) > 0

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @pytest.mark.parametrize(
        ("relevance", "query", "expected_results"),
        [
            (0.2, "safety factor", True),
            (0.8, "safety factor", False),
            (0.2, "", True),
            (0.0, "", True),
        ],
    )
    @staticmethod
    def test_perform_search_functionality(relevance, query, expected_results):
        """Test the perform_search function with real services."""
        # Test search execution
        (
            results,
            download_dict,
            message,
            doc_plot,
            mode_plot,
            year_hist,
            agency_plot,
            event_plot,
        ) = perform_search(
            username="testuser",
            query=query,
            year_range_start=2010,
            year_range_end=2024,
            document_type=["Safety issues"],
            modes=[0, 1, 2],
            agencies=["TAIC"],
            relevance=relevance,
        )

        assert isinstance(results, pd.DataFrame)
        assert results.empty != expected_results
        assert isinstance(message, str)
        assert download_dict is not None
        assert "settings" in download_dict
        assert "results" in download_dict
        if expected_results:
            assert doc_plot is not None
            assert mode_plot is not None
            assert year_hist is not None
            assert agency_plot is not None
            assert event_plot is not None
        else:
            assert doc_plot is None
            assert mode_plot is None
            assert year_hist is None
            assert agency_plot is None
            assert event_plot is None


class TestConversationFunctions:
    """Tests for conversation-related functions in the app."""

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @staticmethod
    def test_conversation_functions():
        """Test conversation-related functions."""
        # Test getting user conversations (should work even with no conversations)
        conversations = get_user_conversations_metadata(Mock(username="testuser"))
        assert isinstance(conversations, list)
        assert len(conversations) >= 0

        # Test creating/updating conversation
        conversation_id = str(uuid.uuid4())
        history = CompleteHistory([])
        history.add_message("user", "Test message")
        history.add_message("assistant", "Test response")

        string_to_avoid = "Failed to store conversation"
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            results = create_or_update_conversation(
                Mock(username="testuser"),
                conversation_id=conversation_id,
                history=history,
                conversation_title="Test Conversation",
            )
        output = buf.getvalue()
        assert string_to_avoid not in output
        assert isinstance(results, str)

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @staticmethod
    def test_load_nonexistent_conversation_function():
        """Test loading conversations."""
        # Test loading non-existent conversation
        conversation_id = str(uuid.uuid4())
        history, gradio_format, conv_id, title, _btn = load_conversation(
            Mock(username="testuser"),
            conversation_id,
        )

        assert len(history) == 0
        assert gradio_format == []
        assert conv_id == f"{conversation_id}"
        assert title == "Failed to load"

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @staticmethod
    def test_load_conversation_function():
        """Test loading a real conversation."""
        conversation_id = str(uuid.uuid4())
        history = CompleteHistory([])
        history.add_message("user", "Test load")
        history.add_message("assistant", "Test response")

        create_or_update_conversation(
            Mock(username="testuser"),
            conversation_id=conversation_id,
            history=history,
            conversation_title="Test Conversation",
        )

        loaded_history, _gradio_format, _conv_id, _title, _btn = load_conversation(
            Mock(username="testuser"),
            conversation_id,
        )

        assert isinstance(loaded_history, list)
        assert len(loaded_history) > 0

        conversation_store.delete_conversation("testuser", conversation_id)

    @pytest.mark.skipif(
        not os.getenv("TEST_USE_REAL_SERVICES"),
        reason="Requires real services",
    )
    @staticmethod
    def test_delete_conversation_function():
        """Test the delete_conversation function with real services."""
        # First, create a conversation
        conversation_id = str(uuid.uuid4())
        history = CompleteHistory([])
        history.add_message("user", "Test message for delete")
        history.add_message("assistant", "Test response")

        # Create the conversation
        create_or_update_conversation(
            Mock(username="testuser"),
            conversation_id=conversation_id,
            history=history,
            conversation_title="Test Conversation for Delete",
        )

        # Now delete it
        request = Mock(username="testuser")
        current_conv = CompleteHistory([])
        chatbot = gr.Chatbot(value=[])
        current_conv_id = None  # Not the one being deleted
        current_conv_title = None
        to_delete = conversation_id

        result = delete_conversation(
            request,
            current_conv,
            chatbot,
            current_conv_id,
            current_conv_title,
            to_delete,
        )

        # Check that it returned success (no clear since current_conv_id != to_delete)
        assert result[0] == current_conv
        assert result[1] == chatbot
        assert result[2] == current_conv_id
        assert result[3] == current_conv_title
        assert not result[4].visible  # Since current_conv_id is None

        loaded_history, _, _, _, _ = load_conversation(
            Mock(username="testuser"),
            conversation_id,
        )
        assert len(loaded_history) == 0  # Should be empty if deleted

    @staticmethod
    def test_delete_conversation_cancelled():
        """Test the delete_conversation function when deletion is cancelled."""
        request = Mock(username="testuser")
        current_conv = CompleteHistory([])
        current_conv.add_message("user", "test")
        chatbot = gr.Chatbot(value=current_conv.gradio_format())
        current_conv_id = "test_conv_id"
        current_conv_title = "Test Title"
        to_delete = None  # Cancelled

        result = delete_conversation(
            request,
            current_conv,
            chatbot,
            current_conv_id,
            current_conv_title,
            to_delete,
        )

        # Should return the same state, no changes
        assert result[0] == current_conv
        assert result[1] == chatbot
        assert result[2] == current_conv_id
        assert result[3] == current_conv_title
        assert result[
            4
        ].visible  # Button should be visible since current_conv_id is not None

    @staticmethod
    def test_handle_example_select():
        """Test the handle_example_select function realistically."""
        selection = Mock()
        selection.value = {"text": "Example question text"}
        current_conversation = CompleteHistory([])

        result = handle_example_select(selection, current_conversation)

        # Check that the user message was added to the history
        assert len(result[2]) == 1
        assert result[2][0]["ai"]["role"] == "user"
        assert result[2][0]["ai"]["content"] == "Example question text"

        # Check the returned components
        assert not result[0].interactive
        assert result[0].value is None
        assert result[1] == "Example question text"
        assert result[3] == result[2].gradio_format()  # Should be the gradio format
        assert isinstance(result[4], str)  # conversation_id
