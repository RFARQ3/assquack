import asyncio
from pathlib import Path

import duckdb
import pytest

from assquack import AssquackConfig, ExportsConfig, asset
from assquack._errors import ExportError


def test_disabled_export_fails_after_table_materialization(tmp_path: Path) -> None:
    called = False

    @asset("bronze/groups.parquet", table="main.groups")
    async def groups() -> list[dict[str, int]]:
        nonlocal called
        called = True
        return [{"id": 1}]

    config = AssquackConfig(
        home=tmp_path,
        exports=ExportsConfig(enabled=False),
    )

    with pytest.raises(
        ExportError,
        match=(
            r"Export target 'bronze/groups\.parquet' was declared, "
            r"but exports are disabled"
        ),
    ) as error:
        asyncio.run(groups(assquack_config=config))

    assert called is True
    assert "ASSQUACK_EXPORTS__ENABLED=true" in str(error.value)

    connection = duckdb.connect(str(config.resolved_database_path))
    try:
        assert connection.execute("SELECT count(*) FROM main.groups").fetchone() == (1,)
        assert connection.execute(
            "SELECT status FROM assquack_meta.runs ORDER BY runtime DESC LIMIT 1"
        ).fetchone() == ("success",)
    finally:
        connection.close()
