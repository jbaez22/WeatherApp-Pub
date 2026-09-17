"""
Unit tests for backend/lambda/secrets_manager.py

Uses unittest.mock to avoid any real AWS calls.
Covers: successful fetch, module-level cache hit, ClientError,
BotoCoreError, and custom secret name from environment variable.
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import BotoCoreError, ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

import secrets_manager as secrets_module
from secrets_manager import SecretsError, _clear_cache, get_api_key


@pytest.fixture(autouse=True)
def clear_module_cache():
    """Reset the module-level cache before every test."""
    _clear_cache()
    yield
    _clear_cache()


def _make_client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": ""}}, "GetSecretValue")


class TestGetApiKey:
    def test_returns_api_key_on_success(self):
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": "test-api-key-123"}
        with patch("secrets_manager.boto3.client", return_value=mock_sm):
            key = get_api_key()
        assert key == "test-api-key-123"

    def test_secretsmanager_called_with_correct_secret_id(self):
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": "key"}
        with patch("secrets_manager.boto3.client", return_value=mock_sm):
            get_api_key()
        mock_sm.get_secret_value.assert_called_once_with(
            SecretId="weather-dashboard/openweathermap-api-key",
        )

    def test_module_level_cache_skips_second_call(self):
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": "cached-key"}
        with patch("secrets_manager.boto3.client", return_value=mock_sm):
            first = get_api_key()
            second = get_api_key()

        assert first == second == "cached-key"
        assert mock_sm.get_secret_value.call_count == 1

    def test_secret_not_found_raises_secrets_error(self):
        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = _make_client_error(
            "ResourceNotFoundException"
        )
        with patch("secrets_manager.boto3.client", return_value=mock_sm):
            with pytest.raises(SecretsError, match="ResourceNotFoundException"):
                get_api_key()

    def test_access_denied_raises_secrets_error(self):
        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = _make_client_error("AccessDeniedException")
        with patch("secrets_manager.boto3.client", return_value=mock_sm):
            with pytest.raises(SecretsError, match="AccessDeniedException"):
                get_api_key()

    def test_boto_core_error_raises_secrets_error(self):
        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = BotoCoreError()
        with patch("secrets_manager.boto3.client", return_value=mock_sm):
            with pytest.raises(SecretsError, match="Unexpected error"):
                get_api_key()

    def test_custom_secret_name_from_env(self):
        custom_name = "custom/path/key"
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": "env-key"}
        # Patch the module-level variable directly — avoids module reload which
        # would create a new SecretsError class and break isinstance checks elsewhere.
        with patch.object(secrets_module, "_SECRET_NAME", custom_name), \
             patch("secrets_manager.boto3.client", return_value=mock_sm):
            key = get_api_key()

        assert key == "env-key"
        call_args = mock_sm.get_secret_value.call_args
        assert call_args[1]["SecretId"] == custom_name
