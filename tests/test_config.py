from pathlib import Path

from assquack import AssquackConfig, ExportsConfig, IngestionConfig


def test_process_environment_populates_the_full_config_tree(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ASSQUACK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ASSQUACK_ENVIRONMENT", "test")
    monkeypatch.setenv("ASSQUACK_DATABASE_PATH", str(tmp_path / "database.duckdb"))
    monkeypatch.setenv("ASSQUACK_CHUNK_SIZE", "250")
    monkeypatch.setenv("ASSQUACK_INGESTION__FRAGMENT_MAX_ROWS", "5000")
    monkeypatch.setenv(
        "ASSQUACK_INGESTION__FRAGMENT_DIRECTORY",
        str(tmp_path / "ingestion"),
    )
    monkeypatch.setenv("ASSQUACK_DUCKDB__THREADS", "8")
    monkeypatch.setenv("ASSQUACK_DUCKDB__MEMORY_LIMIT", "4GB")
    monkeypatch.setenv(
        "ASSQUACK_DUCKDB__TEMP_DIRECTORY",
        str(tmp_path / "spill"),
    )
    monkeypatch.setenv("ASSQUACK_DUCKDB__EXTENSIONS", '["json", "parquet"]')
    monkeypatch.setenv("ASSQUACK_EXPORTS__ENABLED", "true")
    monkeypatch.setenv("ASSQUACK_EXPORTS__BASE_URI", str(tmp_path / "exports"))
    monkeypatch.setenv("ASSQUACK_EXPORTS__DEFAULT_FORMAT", "parquet")
    monkeypatch.setenv("ASSQUACK_EXPORTS__SNAPSHOT_RUNS", "true")
    monkeypatch.setenv(
        "ASSQUACK_EXPORTS__OPTIONS",
        '{"compression": "zstd", "overwrite": true}',
    )

    config = AssquackConfig()

    assert config.home == tmp_path / "home"
    assert config.environment == "test"
    assert config.database_path == tmp_path / "database.duckdb"
    assert config.chunk_size == 250
    assert config.ingestion.fragment_max_rows == 5_000
    assert config.ingestion.fragment_directory == tmp_path / "ingestion"
    assert config.duckdb.threads == 8
    assert config.duckdb.memory_limit == "4GB"
    assert config.duckdb.temp_directory == tmp_path / "spill"
    assert config.duckdb.extensions == ("json", "parquet")
    assert config.exports.enabled is True
    assert config.exports.base_uri == str(tmp_path / "exports")
    assert config.exports.default_format == "parquet"
    assert config.exports.snapshot_runs is True
    assert config.exports.options == {"compression": "zstd", "overwrite": True}


def test_fragment_directory_defaults_beside_database(tmp_path: Path) -> None:
    database = tmp_path / "state" / "custom.duckdb"

    config = AssquackConfig(database_path=database)

    assert config.resolved_fragment_directory == database.parent / "fragments"


def test_direct_ingestion_config_controls_fragment_size() -> None:
    config = AssquackConfig(
        ingestion=IngestionConfig(fragment_max_rows=25),
    )

    assert config.ingestion.fragment_max_rows == 25


def test_direct_values_override_environment(monkeypatch) -> None:
    monkeypatch.setenv("ASSQUACK_ENVIRONMENT", "environment-value")
    monkeypatch.setenv("ASSQUACK_EXPORTS__ENABLED", "true")

    config = AssquackConfig(
        environment="direct-value",
        exports=ExportsConfig(enabled=False),
    )

    assert config.environment == "direct-value"
    assert config.exports.enabled is False


def test_documented_environment_names_remain_supported(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "legacy-environment")
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(tmp_path / "legacy-spill"))

    config = AssquackConfig()

    assert config.environment == "legacy-environment"
    assert config.duckdb.temp_directory == tmp_path / "legacy-spill"
