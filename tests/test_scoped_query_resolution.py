import asyncio
import copy

import duckdb
import pytest

import assquack
from assquack import AssquackConfig, asset


@pytest.fixture
def scoped_catalog(tmp_path):
    config = AssquackConfig(home=tmp_path, attachment_name="people")
    config.attach()
    duckdb.execute("CREATE TABLE people.main.costs AS SELECT 2 id")
    duckdb.execute("CREATE TABLE memory.main.costs AS SELECT 999 id")
    duckdb.execute("CREATE SCHEMA people.hr")
    duckdb.execute("CREATE TABLE people.hr.costs AS SELECT 3 id")
    duckdb.execute("CREATE SCHEMA memory.hr")
    duckdb.execute("CREATE TABLE memory.hr.costs AS SELECT 999 id")
    duckdb.execute('CREATE TABLE people.main."odd.costs" AS SELECT 4 id')
    duckdb.execute('CREATE TABLE people.main."a b" AS SELECT 5 id')
    return config


def test_qualified_query_table_macro_fails_clearly(scoped_catalog):
    duckdb.execute("CREATE MACRO people.main.query_table(x) AS TABLE SELECT x AS val")
    with (
        assquack.use_config(scoped_catalog),
        pytest.raises(ValueError, match="Qualified query_table"),
    ):
        asyncio.run(assquack.query("SELECT * FROM people.main.query_table('hello')"))


@pytest.mark.parametrize(
    "name,expected",
    [
        ("costs", 2),
        ("people.main.costs", 2),
        ("hr.costs", 3),
        ('"people"."hr"."costs"', 3),
        ('"odd.costs"', 4),
        ('"people"."main"."odd.costs"', 4),
    ],
)
@pytest.mark.parametrize("query_table", [False, True])
def test_scoped_name_forms(scoped_catalog, name, expected, query_table):
    source = "query_table(?)" if query_table else name
    params = [name] if query_table else None
    with assquack.use_config(scoped_catalog):
        relation = asyncio.run(assquack.query(f"SELECT id FROM {source}", params))
    assert isinstance(relation, duckdb.DuckDBPyRelation)
    assert relation.fetchone() == (expected,)
    assert duckdb.sql("SELECT current_database()").fetchone() == ("memory",)


@pytest.mark.parametrize(
    "sql,params,expected",
    [
        ("SELECT id FROM query_table('costs')", None, [(2,)]),
        (
            "SELECT id FROM query_table(['costs','hr.costs'],true) ORDER BY id",
            None,
            [(2,), (3,)],
        ),
        (
            "SELECT id FROM query_table($tables,true) ORDER BY id",
            {"tables": ["costs", "hr.costs"]},
            [(2,), (3,)],
        ),
        ("SELECT id FROM query_table($table)", {"table": "people.main.costs"}, [(2,)]),
        (
            "SELECT id FROM query_table(?,true) WHERE id > ? ORDER BY id",
            [["costs", "hr.costs"], 2],
            [(3,)],
        ),
        ("SELECT id FROM query_table($1) WHERE id = $2", ["costs", 2], [(2,)]),
        ("SELECT id FROM query_table('a b')", None, [(5,)]),
    ],
)
def test_query_table_argument_forms(scoped_catalog, sql, params, expected):
    original = copy.deepcopy(params)
    with assquack.use_config(scoped_catalog):
        relation = asyncio.run(assquack.query(sql, params))
    assert relation.fetchall() == expected
    assert params == original


def test_parameter_reuse_keeps_original_value_and_lazy_binding(scoped_catalog):
    params = {"tables": ["costs", "hr.costs"]}
    with assquack.use_config(scoped_catalog):
        rows = asyncio.run(
            assquack.query(
                "SELECT $tables requested, id FROM query_table($tables,true) "
                "ORDER BY id",
                params,
            )
        )
    params["tables"][:] = ["memory.main.costs"]
    assert rows.fetchall() == [(["costs", "hr.costs"], 2), (["costs", "hr.costs"], 3)]
    assert duckdb.sql("SELECT sum(id) FROM rows").fetchone() == (5,)


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("WITH costs AS (SELECT 7 id) SELECT id FROM query_table('costs')", [(7,)]),
        ("WITH costs AS (SELECT id FROM costs) SELECT * FROM costs", [(2,)]),
        (
            "WITH RECURSIVE costs AS (SELECT id FROM costs UNION ALL "
            "SELECT id+1 FROM costs WHERE id<3) SELECT * FROM costs",
            [(2,), (3,)],
        ),
        (
            "WITH RECURSIVE n(id) AS "
            "(SELECT 1 UNION ALL SELECT id+1 FROM n WHERE id<3) SELECT * FROM n",
            [(1,), (2,), (3,)],
        ),
    ],
)
def test_cte_resolution_is_preserved(scoped_catalog, sql, expected):
    with assquack.use_config(scoped_catalog):
        result = asyncio.run(assquack.query(sql))
    assert result.fetchall() == expected


def test_replacement_scan_and_explicit_cross_catalog_join(scoped_catalog):
    native_rows = duckdb.sql("SELECT 2 id")  # noqa: F841
    with assquack.use_config(scoped_catalog):
        result = asyncio.run(
            assquack.query(
                "SELECT p.id FROM query_table('native_rows') n "
                "JOIN people.main.costs p USING(id) "
                "JOIN memory.main.costs m ON m.id=999"
            )
        )
    assert result.fetchall() == [(2,)]


def test_two_part_names_are_schema_table(scoped_catalog):
    duckdb.execute("CREATE SCHEMA people.people")
    duckdb.execute("CREATE TABLE people.people.costs AS SELECT 8 id")
    with assquack.use_config(scoped_catalog):
        for sql in [
            "SELECT * FROM people.costs",
            "SELECT * FROM query_table('people.costs')",
        ]:
            assert asyncio.run(assquack.query(sql)).fetchone() == (8,)
        assert asyncio.run(
            assquack.query("SELECT id FROM people.people.costs")
        ).fetchone() == (8,)


def test_snapshot_table_list_from_metadata_with_union_by_name(scoped_catalog):
    duckdb.execute("CREATE SCHEMA people.history")
    duckdb.execute("CREATE SCHEMA memory.history")
    for name, extra in [("snapshot_01", "1 score"), ("snapshot_02", "'active' status")]:
        duckdb.execute(f"CREATE TABLE people.history.{name} AS SELECT 2 id, {extra}")
        duckdb.execute(f"CREATE TABLE memory.history.{name} AS SELECT 999 id, {extra}")
    snapshot_tables = [
        row[0]
        for row in duckdb.sql(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_catalog='people' AND table_schema='history' "
            "ORDER BY table_name"
        ).fetchall()
    ]

    @asset("reports.combined", config=scoped_catalog)
    async def combined():
        return await assquack.query(
            "SELECT * FROM query_table($snapshot_tables,true)",
            {"snapshot_tables": snapshot_tables},
        )

    asyncio.run(combined())
    result = asyncio.run(
        combined.query(
            "SELECT id, score, status FROM query_table(?) ORDER BY score NULLS LAST",
            ["reports.combined"],
        )
    )
    assert result.fetchall() == [(2, 1, None), (2, None, "active")]


@pytest.fixture
def multiple_catalogs(scoped_catalog, tmp_path):
    other = AssquackConfig(home=tmp_path / "other", attachment_name="other")
    other.attach()
    duckdb.execute("CREATE TABLE people.main.unique_costs AS SELECT 2 id")
    duckdb.execute("CREATE TABLE other.main.costs AS SELECT 3 id")
    return scoped_catalog, other


@pytest.mark.parametrize("scope", [False, True])
def test_one_database_without_scope_and_retained_binding(
    scoped_catalog, tmp_path, scope
):
    def bind():
        return asyncio.run(assquack.query("SELECT id FROM costs"))

    if scope:
        with assquack.use_config(scoped_catalog):
            rows = bind()
    else:
        rows = bind()
    AssquackConfig(home=tmp_path / "later", attachment_name="later").attach()
    assert rows.fetchall() == [(2,)]
    with pytest.raises(ValueError, match=r"database\.schema\.table"):
        bind()


@pytest.mark.parametrize(
    "sql,params",
    [
        ("SELECT * FROM costs", None),
        ("SELECT * FROM main.costs", None),
        ("SELECT * FROM people.costs", None),
        ("SELECT * FROM unique_costs", None),
        ("SELECT * FROM missing", None),
        ("SELECT * FROM query_table('costs')", None),
        ("SELECT * FROM query_table(['people.main.costs','costs'],true)", None),
        ("SELECT * FROM query_table(?)", ["costs"]),
        ("SELECT * FROM query_table($names,true)", {"names": ["main.costs"]}),
        (
            "WITH RECURSIVE costs AS (SELECT id FROM costs UNION ALL "
            "SELECT id+1 FROM costs WHERE id<3) SELECT * FROM costs",
            None,
        ),
        ("WITH costs AS (SELECT * FROM costs) SELECT * FROM costs", None),
    ],
)
def test_multiple_databases_require_full_names(multiple_catalogs, sql, params):
    for config in multiple_catalogs:
        with (
            assquack.use_config(config),
            pytest.raises(ValueError, match=r"database\.schema\.table"),
        ):
            asyncio.run(assquack.query(sql, params))
    with pytest.raises(ValueError, match=r"database\.schema\.table"):
        asyncio.run(assquack.query(sql, params))


@pytest.mark.parametrize("name", ["main.query_table", "system.main.query_table"])
def test_qualified_builtin_never_falls_back_to_memory(scoped_catalog, name):
    with pytest.raises(ValueError, match="Qualified query_table"):
        asyncio.run(assquack.query(f"SELECT * FROM {name}('costs')"))


def test_missing_single_database_table_never_falls_back_to_memory(scoped_catalog):
    duckdb.execute("CREATE TABLE memory.main.missing AS SELECT 999 id")
    for source in ["missing", "query_table('missing')"]:
        with pytest.raises(duckdb.CatalogException):
            asyncio.run(assquack.query(f"SELECT * FROM {source}"))


@pytest.mark.parametrize("temporary", [False, True])
def test_physical_names_cannot_bypass_rule_as_python_or_file_scans(
    multiple_catalogs, temporary
):
    costs = duckdb.sql("SELECT 7 id")  # noqa: F841
    prefix = "TEMP" if temporary else ""
    duckdb.execute(f'CREATE {prefix} VIEW "costs.csv" AS SELECT 999 id')
    for source in ["costs", '"costs.csv"', "query_table('costs')"]:
        with pytest.raises(ValueError, match=r"database\.schema\.table"):
            asyncio.run(assquack.query(f"SELECT * FROM {source}"))


def test_single_database_replacement_collision_cannot_read_memory(scoped_catalog):
    missing = duckdb.sql("SELECT 7 id")  # noqa: F841
    duckdb.execute("CREATE TABLE memory.main.missing AS SELECT 999 id")
    # DuckDB can replacement-scan even a qualified missing table. The native
    # Python relation remains valid, but the memory table must not win.
    assert asyncio.run(assquack.query("SELECT * FROM missing")).fetchall() == [(7,)]


def test_multiple_database_exemptions_and_full_names(multiple_catalogs):
    native_rows = duckdb.sql("SELECT 2 id")  # noqa: F841
    rows = asyncio.run(
        assquack.query(
            "SELECT p.id, o.id FROM people.main.costs p "
            "CROSS JOIN other.main.costs o JOIN native_rows n ON n.id=p.id"
        )
    )
    assert rows.fetchall() == [(2, 3)]
    for source in ["native_rows", "query_table('native_rows')"]:
        assert asyncio.run(assquack.query(f"SELECT * FROM {source}")).fetchall() == [
            (2,)
        ]
    assert asyncio.run(
        assquack.query(
            "WITH RECURSIVE costs AS (SELECT id FROM people.main.costs UNION ALL "
            "SELECT id+1 FROM costs WHERE id<3) SELECT * FROM costs"
        )
    ).fetchall() == [(2,), (3,)]
    assert asyncio.run(
        assquack.query("WITH costs AS (SELECT 7 id) SELECT * FROM query_table('costs')")
    ).fetchall() == [(7,)]


def test_fully_qualified_parameter_list_and_asset_alias(multiple_catalogs):
    people, _ = multiple_catalogs
    names = ["people.main.costs", "other.main.costs"]
    params = {"names": names}
    relation = asyncio.run(
        assquack.query(
            "SELECT $names original, id FROM query_table($names,true) ORDER BY id",
            params,
        )
    )
    assert params == {"names": names}
    assert relation.fetchall() == [(names, 2), (names, 3)]

    @asset("main.output", config=people)
    def output():
        return [{"id": 2}]

    result = asyncio.run(output())
    assert asyncio.run(
        result.query("SELECT d.id, o.id FROM data d CROSS JOIN other.main.costs o")
    ).fetchall() == [(2, 3)]
    with pytest.raises(ValueError, match=r"database\.schema\.table"):
        asyncio.run(result.query("SELECT * FROM costs"))
