from pathlib import Path

from midas_data.settings import Settings


def test_configuration_repr_never_exposes_credentials_or_connection_urls():
    settings = Settings(
        database_dsn="postgresql://secret-db", lake_catalog_dsn="password=secret-lake",
        api_key="secret-api", nats_url="nats://secret-broker",
        clickhouse_url="http://secret-endpoint", clickhouse_user="reader",
        clickhouse_password="secret-clickhouse", raw_path=Path("/raw"), lake_path="/lake",
    )
    rendered = repr(settings)
    assert "secret-" not in rendered
    assert "reader" in rendered