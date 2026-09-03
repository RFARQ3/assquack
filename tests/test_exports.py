import asyncio

import pytest

from assquack import AssquackConfig, ExportsConfig, asset
from assquack._errors import ExportError


def test_declared_export_fails_clearly_when_exports_are_disabled() -> None:
    called = False

    @asset("bronze/groups.parquet")
    async def groups() -> list[dict[str, int]]:
        nonlocal called
        called = True
        return [{"id": 1}]

    config = AssquackConfig(exports=ExportsConfig(enabled=False))

    with pytest.raises(
        ExportError,
        match="Export target 'bronze/groups.parquet' was declared, but exports are disabled",
    ) as error:
        asyncio.run(groups(assquack_config=config))

    assert called is False
    assert "ASSQUACK_EXPORTS__ENABLED=true" in str(error.value)
