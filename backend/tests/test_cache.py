"""
Unit tests for backend/lambda/cache.py

Uses moto to mock DynamoDB — no real AWS calls are made.
Covers: cache miss, cache hit, expired item, write path,
DynamoDB errors on read, and non-fatal behaviour on write failure.
"""

import sys
import os
import time
from unittest.mock import MagicMock, patch

import boto3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

# moto must be imported before the module under test so that boto3 calls
# inside cache.py are intercepted.
try:
    from moto import mock_aws  # moto >= 5
except ImportError:
    from moto import mock_dynamodb as mock_aws  # moto < 5

from cache import CacheError, get_cached_weather, get_stale_cached_weather, set_cached_weather

_TABLE_NAME = "WeatherCache"
_SAMPLE_DATA = {
    "current": {"name": "London", "main": {"temp": 15}},
    "forecast": {"list": []},
}


def _create_table(region: str = "us-east-1"):
    """Create the WeatherCache table in the mocked DynamoDB."""
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.create_table(
        TableName=_TABLE_NAME,
        KeySchema=[{"AttributeName": "city", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "city", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@mock_aws
class TestGetCachedWeather:
    def setup_method(self, method=None):
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
        os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
        os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
        _create_table()

    def test_cache_miss_returns_none(self):
        result = get_cached_weather("london")
        assert result is None

    def test_cache_hit_returns_data(self):
        # Write directly to DynamoDB then verify read
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.Table(_TABLE_NAME)
        table.put_item(
            Item={
                "city": "paris",
                "data": _SAMPLE_DATA,
                "ttl": int(time.time()) + 900,
                "cached_at": "2026-07-02T00:00:00Z",
            }
        )
        result = get_cached_weather("paris")
        assert result is not None
        assert result["current"]["name"] == "London"

    def test_expired_item_treated_as_miss(self):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.Table(_TABLE_NAME)
        table.put_item(
            Item={
                "city": "berlin",
                "data": _SAMPLE_DATA,
                "ttl": int(time.time()) - 1,  # already expired
                "cached_at": "2026-07-01T00:00:00Z",
            }
        )
        result = get_cached_weather("berlin")
        assert result is None

    def test_dynamodb_client_error_raises_cache_error(self):
        from botocore.exceptions import ClientError
        mock_table = MagicMock()
        mock_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": ""}},
            "GetItem",
        )
        with patch("cache._get_table", return_value=mock_table):
            with pytest.raises(CacheError, match="Failed to read"):
                get_cached_weather("london")


@mock_aws
class TestGetStaleCachedWeather:
    def setup_method(self, method=None):
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
        os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
        os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
        _create_table()

    def test_no_cache_entry_returns_none(self):
        result = get_stale_cached_weather("london")
        assert result is None

    def test_expired_item_is_still_returned(self):
        """The whole point of this function: TTL expiry is ignored."""
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.Table(_TABLE_NAME)
        table.put_item(
            Item={
                "city": "berlin",
                "data": _SAMPLE_DATA,
                "ttl": int(time.time()) - 3600,  # expired an hour ago
                "cached_at": "2026-07-01T00:00:00Z",
            }
        )
        result = get_stale_cached_weather("berlin")
        assert result is not None
        assert result["current"]["name"] == "London"

    def test_fresh_item_is_also_returned(self):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.Table(_TABLE_NAME)
        table.put_item(
            Item={
                "city": "paris",
                "data": _SAMPLE_DATA,
                "ttl": int(time.time()) + 900,
                "cached_at": "2026-07-02T00:00:00Z",
            }
        )
        result = get_stale_cached_weather("paris")
        assert result is not None

    def test_dynamodb_client_error_returns_none_not_exception(self):
        """Unlike get_cached_weather, this must never raise — a fallback
        that can itself fail isn't a usable fallback."""
        from botocore.exceptions import ClientError
        mock_table = MagicMock()
        mock_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": ""}},
            "GetItem",
        )
        with patch("cache._get_table", return_value=mock_table):
            result = get_stale_cached_weather("london")
        assert result is None


@mock_aws
class TestSetCachedWeather:
    def setup_method(self, method=None):
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
        os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
        os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
        _create_table()

    def test_write_then_read_returns_same_data(self):
        set_cached_weather("madrid", _SAMPLE_DATA)
        result = get_cached_weather("madrid")
        assert result == _SAMPLE_DATA

    def test_ttl_set_approximately_900_seconds_in_future(self):
        set_cached_weather("rome", _SAMPLE_DATA)
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        item = dynamodb.Table(_TABLE_NAME).get_item(Key={"city": "rome"})["Item"]
        ttl = int(item["ttl"])
        now = int(time.time())
        assert 895 <= ttl - now <= 905

    def test_write_failure_does_not_raise(self):
        """Cache write failures must be non-fatal (caller still has the data)."""
        from botocore.exceptions import ClientError
        mock_table = MagicMock()
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": ""}},
            "PutItem",
        )
        with patch("cache._get_table", return_value=mock_table):
            # Should not raise
            set_cached_weather("lisbon", _SAMPLE_DATA)

    def test_overwrite_updates_existing_entry(self):
        updated = {"current": {"name": "London Updated"}, "forecast": {"list": []}}
        set_cached_weather("london", _SAMPLE_DATA)
        set_cached_weather("london", updated)
        result = get_cached_weather("london")
        assert result["current"]["name"] == "London Updated"
