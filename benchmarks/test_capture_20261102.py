"""The bounded oracle must reject changed, missing and duplicate fixture records."""

import duckdb
import pytest
from capture_20261102 import query, validate_records


@pytest.fixture
def connection():
    with duckdb.connect() as opened:
        opened.execute("CREATE TABLE source AS SELECT range AS id FROM range(10)")
        opened.execute(
            "CREATE TABLE dimension AS SELECT range AS key, "
            "0::BIGINT AS offset_value FROM range(1000)"
        )
        yield opened


@pytest.mark.parametrize("shape", ["flat", "nested", "wide"])
@pytest.mark.parametrize("plan", ["scan", "join"])
def test_accepts_every_fixture_shape_and_plan(connection, shape, plan):
    connection.execute("CREATE VIEW observed AS " + query(shape, plan))
    validate_records(connection, shape, 10)


@pytest.mark.parametrize(
    "alteration",
    [
        "SELECT * REPLACE (false AS active) FROM original",
        "SELECT * FROM original WHERE id < 9",
        "SELECT * FROM original WHERE id < 9 UNION ALL "
        "SELECT * FROM original WHERE id = 8",
        "SELECT * REPLACE (id + 1 AS id) FROM original",
    ],
)
def test_rejects_changed_or_incomplete_records(connection, alteration):
    connection.execute("CREATE VIEW original AS " + query("flat", "scan"))
    connection.execute("CREATE VIEW observed AS " + alteration)
    with pytest.raises(AssertionError):
        validate_records(connection, "flat", 10)
