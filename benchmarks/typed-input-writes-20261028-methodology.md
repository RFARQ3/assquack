# Typed-input NDJSON write diagnostic

Offline synthetic benchmark only. No production imports or modifications.

Compare pandas DataFrame, explicitly typed PyArrow Table and explicitly typed
PyArrow RecordBatch inputs independently. The two common routes are direct native
DuckDB relation registration/COPY and bounded conversion to Python records with
stdlib compact JSON, buffered binary writes and whole-record rotation.

One additional relevant route is pandas' own `DataFrame.to_json(orient="records",
lines=True, force_ascii=False, double_precision=15)`, called in bounded slices.
It produces NDJSON directly, avoiding Python record dictionaries. Encoding its
text to UTF-8, splitting at record boundaries and buffered fragment writes are
included. It is eligible only if exact-value validation passes. No additional
Arrow-specific route or alternate format is introduced.

Fixtures are constructed column by column, with explicit Arrow schema and pandas
dtypes, never by converting a Python record dataset to Arrow. Narrow and wide
share integer, finite exactly representable float, boolean, Unicode/escaping,
nullable integer, nullable fixed-field struct and nullable integer-list fields.
Wide adds twelve text fields. Null is JSON null; integer/float/bool distinctions,
all keys, nesting and exact finite values must survive. No absent-key padding or
numeric widening is allowed. Dates, decimals, binary, NaN, infinity, categoricals,
heterogeneous objects and arbitrary extension dtypes are outside this experiment.

Use prior row counts: narrow 1,000/10,000/100,000; wide 100/1,000/10,000.
Targets are 65,536/262,144/1,048,576 bytes and Python batch sizes 128/2,048.
Three repetitions use seeded shuffled order after discarded route warmups.
Every workload holds the existing OS byte-range exclusive timing lock.
Input construction and full-record validation are untimed. Complete write time
includes connection creation/close for COPY, source registration/conversion,
serialization, actual writes, flush/close, discovery, and empty-file removal.
COPY sizing is approximate; Python closes after a whole record reaches target.
No fsync or timed reads. Output sizes/counts and transient empty removals are
retained. All records are checked against an independent scalar oracle outside
timing, with exact scalar types. Each trial uses a fresh output directory.
