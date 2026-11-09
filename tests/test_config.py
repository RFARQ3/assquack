import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assquack import (
    AssquackConfig,
    FileSystemDestination,
    IngestionConfig,
    SchemaConfig,
)


@pytest.mark.parametrize("source", ["python", "environment"])
@pytest.mark.parametrize(
    "path", ["E:/data/namely/exports", r"E:\data\namely\exports", "./exports"]
)
def test_plain_export_path_uses_default_auth(monkeypatch, source, path):
    if source == "environment":
        monkeypatch.setenv("ASSQUACK_EXPORTS", path)
        config = AssquackConfig()
    else:
        config = AssquackConfig(exports=path)

    assert config.export_destination is not None
    destination = config.export_destination
    assert isinstance(destination, FileSystemDestination)
    assert destination.base_uri == path
    assert destination.auth == "auto"


@pytest.mark.parametrize("source", ["python", "environment"])
def test_export_mapping_uses_filesystem_destination(monkeypatch, source):
    value = {"base_uri": "./exports"}
    if source == "environment":
        monkeypatch.setenv("ASSQUACK_EXPORTS", json.dumps(value))
        config = AssquackConfig()
    else:
        config = AssquackConfig.model_validate({"exports": value})
    assert config.export_destination is not None
    assert config.export_destination.model_dump() == {
        "base_uri": "./exports",
        "auth": "auto",
        "path": None,
        "format": None,
    }


@pytest.mark.parametrize("source", ["python", "environment"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_export_lists_are_rejected(monkeypatch, source, count):
    value = [{"base_uri": f"./exports-{index}"} for index in range(count)]
    if source == "environment":
        monkeypatch.setenv("ASSQUACK_EXPORTS", json.dumps(value))
    with pytest.raises(ValidationError, match="one destination or None"):
        if source == "environment":
            AssquackConfig()
        else:
            AssquackConfig.model_validate({"exports": value})


def test_exports_default_to_disabled_and_environment_null_disables(monkeypatch):
    monkeypatch.delenv("ASSQUACK_EXPORTS", raising=False)
    assert AssquackConfig().export_destination is None
    monkeypatch.setenv("ASSQUACK_EXPORTS", "null")
    assert AssquackConfig().export_destination is None


def test_export_path_override_and_destination_instances(monkeypatch):
    monkeypatch.setenv("ASSQUACK_EXPORTS", "./environment")
    config = AssquackConfig(exports="./direct")
    assert config.export_destination is not None
    assert config.export_destination.model_dump() == {
        "base_uri": "./direct",
        "auth": "auto",
        "path": None,
        "format": None,
    }
    destination = FileSystemDestination(base_uri="./instance", auth="none")
    assert AssquackConfig(exports=destination).export_destination is destination
    assert AssquackConfig(exports=None).export_destination is None


def test_process_environment_populates_the_full_config_tree(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ASSQUACK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ASSQUACK_DATABASE_PATH", str(tmp_path / "database.duckdb"))
    monkeypatch.setenv("ASSQUACK_INGESTION__FRAGMENT_TARGET_MB", "0.125")
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
    monkeypatch.setenv("ASSQUACK_EXPORTS", '{"base_uri": "./exports", "auth": "auto"}')

    config = AssquackConfig()

    assert config.home == tmp_path / "home"
    assert config.database_path == tmp_path / "database.duckdb"
    assert config.ingestion.fragment_target_mb == 0.125
    assert config.ingestion.fragment_directory == tmp_path / "ingestion"
    assert config.duckdb.threads == 8
    assert config.duckdb.memory_limit == "4GB"
    assert config.duckdb.temp_directory == tmp_path / "spill"
    assert config.duckdb.extensions == ("json", "parquet")
    assert config.export_destination is not None
    assert config.export_destination.model_dump() == {
        "base_uri": "./exports",
        "auth": "auto",
        "path": None,
        "format": None,
    }


def test_removed_chunk_size_is_rejected() -> None:
    with pytest.raises(ValidationError, match="chunk_size"):
        AssquackConfig.model_validate({"chunk_size": 250})


def test_fragment_directory_defaults_beside_database(tmp_path: Path) -> None:
    database = tmp_path / "state" / "custom.duckdb"

    config = AssquackConfig(database_path=database)

    assert config.resolved_fragment_directory == database.parent / "fragments"


def test_direct_ingestion_config_controls_fragment_size() -> None:
    config = AssquackConfig(
        ingestion=IngestionConfig(fragment_target_mb=0.25),
    )

    assert config.ingestion.fragment_target_mb == 0.25


def test_direct_values_override_environment(monkeypatch) -> None:
    monkeypatch.setenv("ASSQUACK_HOME", "environment-value")
    monkeypatch.setenv("ASSQUACK_EXPORTS", '{"base_uri": "./exports"}')

    config = AssquackConfig(
        home=Path("direct-value"),
        exports=None,
    )

    assert config.home == Path("direct-value")
    assert config.export_destination is None


def test_documented_environment_names_remain_supported(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(tmp_path / "legacy-spill"))

    config = AssquackConfig()

    assert config.duckdb.temp_directory == tmp_path / "legacy-spill"


def test_schema_strategy_environment_and_direct_override(monkeypatch) -> None:
    monkeypatch.delenv("ASSQUACK_SCHEMA__REPLACE", raising=False)
    assert AssquackConfig().schema.replace == "current_run"

    monkeypatch.setenv("ASSQUACK_SCHEMA__REPLACE", "current_run")
    assert AssquackConfig().schema.replace == "current_run"

    monkeypatch.setenv("ASSQUACK_SCHEMA__REPLACE", "continuation")
    with pytest.raises(ValidationError, match="current_run"):
        AssquackConfig()

    config = AssquackConfig(schema=SchemaConfig(replace="current_run"))
    assert config.schema.replace == "current_run"

    with pytest.raises(ValidationError, match="current_run"):
        SchemaConfig.model_validate({"replace": "continuation"})


@pytest.mark.parametrize("target", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_fragment_target_rejects_nonpositive_or_nonfinite_values(target: float) -> None:
    with pytest.raises(ValidationError):
        IngestionConfig(fragment_target_mb=target)


def test_fragment_target_default_and_direct_environment_precedence(monkeypatch) -> None:
    monkeypatch.delenv("ASSQUACK_INGESTION__FRAGMENT_TARGET_MB", raising=False)
    assert IngestionConfig().fragment_target_mb == 64.0

    monkeypatch.setenv("ASSQUACK_INGESTION__FRAGMENT_TARGET_MB", "2.5")
    assert AssquackConfig().ingestion.fragment_target_mb == 2.5
    config = AssquackConfig(ingestion=IngestionConfig(fragment_target_mb=0.00002))
    assert config.ingestion.fragment_target_mb == 0.00002


@pytest.mark.parametrize("variable", ["ASSQUACK_ENVIRONMENT", "ENVIRONMENT"])
def test_environment_names_do_not_select_paths(monkeypatch, tmp_path: Path, variable):
    monkeypatch.setenv(variable, "production")
    monkeypatch.setenv("ASSQUACK_HOME", str(tmp_path))
    config = AssquackConfig()
    assert config.resolved_database_path == tmp_path / "assquack.duckdb"
    assert config.resolved_fragment_directory == tmp_path / "fragments"
    assert "environment" not in config.model_dump()


def test_default_home_is_current_directory(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ASSQUACK_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    config = AssquackConfig()
    assert config.home == tmp_path
    assert config.resolved_database_path == tmp_path / "assquack.duckdb"


def test_environment_is_not_a_config_field():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AssquackConfig.model_validate({"environment": "dev"})
