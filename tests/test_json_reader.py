from __future__ import annotations

import asyncio
from pathlib import Path

import duckdb
import pytest

from assquack import AssquackConfig, asset
from assquack._materialization import json_reader
from assquack._materialization.json_reader import (
    DEFAULT_MAXIMUM_OBJECT_SIZE,
    JsonReadRecovery,
    largest_record_bytes,
    read_json_objects_sql,
    read_json_sql,
)


@pytest.mark.parametrize("buffer_size", [1, 2, 7, 16])
def test_scan_counts_bytes_across_buffers_and_fragments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, buffer_size: int
) -> None:
    monkeypatch.setattr(json_reader, "_SCAN_BUFFER_BYTES", buffer_size)
    first = tmp_path / "first.ndjson"
    second = tmp_path / "second.ndjson"
    record = '{"value":"雪é"}'.encode()
    first.write_bytes(b"{}\r\n" + record + b"\r\n{}\n")
    second.write_bytes(record + b" ")
    assert largest_record_bytes([str(first)]) == len(record)
    assert largest_record_bytes([str(first), str(second)]) == len(record) + 1


def test_normal_read_and_parse_error_do_not_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_scan(paths):
        pytest.fail("normal and malformed JSON reads must not scan")

    monkeypatch.setattr(json_reader, "largest_record_bytes", unexpected_scan)
    path = tmp_path / "records.ndjson"
    path.write_text('{"value":1}\n')
    with duckdb.connect() as connection:
        reader = JsonReadRecovery()

        def read(limit):
            return connection.execute(
                f"SELECT * FROM {read_json_sql([str(path)], limit)}"
            ).fetchall()

        assert reader.read([str(path)], read) == [(1,)]
        path.write_text('{"value":invalid}\n')
        with pytest.raises(duckdb.InvalidInputException, match="Malformed JSON"):
            reader.read([str(path)], read)


def test_actual_size_error_retries_once_and_propagates_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fragment = tmp_path / "large.ndjson"
    with fragment.open("wb") as stream:
        stream.write(b'{"value":"')
        for _ in range(70):
            stream.write(b"x" * (1024 * 1024))
        stream.write(b'"}\n')
    path = str(fragment)
    with duckdb.connect() as connection:
        with pytest.raises(duckdb.InvalidInputException) as captured:
            connection.execute(
                f"SELECT * FROM {read_json_objects_sql(path)}"
            ).fetchall()
        error = captured.value
        assert 'Try increasing "maximum_object_size".' in str(error)
        scans = []

        def scan(paths):
            scans.append(paths)
            return largest_record_bytes(paths)

        monkeypatch.setattr(json_reader, "largest_record_bytes", scan)
        limits = []

        def fail(limit):
            limits.append(limit)
            raise error

        reader = JsonReadRecovery()
        with pytest.raises(duckdb.InvalidInputException) as retried:
            reader.read([path], fail)
        assert retried.value is error
        assert limits == [DEFAULT_MAXIMUM_OBJECT_SIZE, fragment.stat().st_size - 1]
        assert scans == [[path]]
        with pytest.raises(duckdb.InvalidInputException):
            reader.read([path], fail)
        assert len(scans) == 1
        assert len(limits) == 3


def test_large_record_materializes_with_one_scan_and_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scans = []

    def scan(paths):
        scans.append(tuple(paths))
        return largest_record_bytes(paths)

    monkeypatch.setattr(json_reader, "largest_record_bytes", scan)
    extractions = 0

    @asset("main.large_record")
    async def source():
        nonlocal extractions
        extractions += 1
        return [{"value": "雪" * (24 * 1024 * 1024), "asset": "source"}]

    config = AssquackConfig(home=tmp_path)
    result = asyncio.run(source(assquack_config=config))
    assert result.row_count == 1
    assert extractions == 1
    assert len(scans) == 1
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT length(value), asset FROM main.large_record"
        ).fetchall() == [(24 * 1024 * 1024, "source")]
    assert Path(scans[0][0]).exists()
