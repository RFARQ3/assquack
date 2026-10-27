# Credential sourcing: lessons from Dagster

Research date: 26 October 2026. Recommendation only; no scope or defect-status change.

**Recommendation:** downstream environment setup plus native DuckDB identity discovery
is a sufficient default for Assquack's current local/Azure scope. Dagster does not
establish a requirement for public credential-provider injection. Keep the existing
SAS provider internal, and defer a public sourcing contract until a concrete caller
needs credentials that cannot reasonably be supplied through its execution environment.

## What Dagster actually provides

`EnvVar("NAME")` is a reference to an environment variable. In Dagster configuration
it defers resolution until execution/launch configuration is processed and avoids
displaying the resolved secret in configuration UI. It does not retrieve a secret
from an arbitrary vault. OSS deployments must arrange the worker environment;
local development also supports environment-file loading. An `os.getenv()` call
reads when executed: Dagster's warning about code-load timing concerns calls made
while constructing definitions, not every possible use of that Python function.
See [environment-variable documentation](https://dagster.io/docs/guides/operate/configuration/using-environment-variables-and-secrets).

The [EnvVar source](https://github.com/dagster-io/dagster/blob/master/python_modules/dagster/dagster/_config/field_utils.py)
confirms the boundary: `get_value()` calls `os.getenv`, and configuration conversion
represents the reference as an `env` mapping. Passing a Dagster `EnvVar` to an
unrelated Python library does not automatically give that library deferred resolution.

`ConfigurableResource` groups typed configuration and client behavior. Resources
can receive environment references, depend on other resources, or use
`configure_at_launch()` for run-specific configuration. This is an application
composition facility; a resource wrapping Assquack does not require Assquack to
implement Dagster's resource model. See [resource configuration](https://dagster.io/docs/guides/build/external-resources/configuring-resources).

Dagster invokes resource setup and teardown once per run per process; multiprocess
execution creates separate resource instances. A custom resource can retrieve
credentials during setup and retain private state, or use a context manager for
its lifetime. The resource author or underlying SDK must implement any subsequent
refresh: lifecycle hooks alone are not a refresh service. See
[resource lifecycle](https://dagster.io/docs/guides/build/external-resources/managing-resource-state).

Dagster Plus adds a managed environment-variable store and deployment/code-location
scoping. Its UI-managed values are stored encrypted and decrypted for execution;
Hybrid deployments can instead supply variables through agent configuration.
That is storage and injection of configured values, not automatic discovery of
arbitrary Azure credentials. OSS infrastructure owners supply the corresponding
deployment configuration themselves. See [Plus environment management](https://dagster.io/docs/deployment/dagster-plus/management/environment-variables).

An explicit secret-manager integration is a different operation. Dagster's AWS
integration exposes a client for `get_secret_value`; its secrets resource also
provides `secrets_in_environment()` to retrieve selected secrets and temporarily
expose them as environment variables. Such retrieval requires configured access
to that secret manager. It is not performed by `EnvVar`, and the environment
context does not continuously refresh secrets. See
[secrets-manager resource source](https://github.com/dagster-io/dagster/blob/master/python_modules/libraries/dagster-aws/dagster_aws/secretsmanager/resources.py).

## Azure: authentication and sourcing remain separate

Dagster's [Azure Blob resource source](https://github.com/dagster-io/dagster/blob/master/python_modules/libraries/dagster-azure/dagster_azure/blob/resources.py)
accepts SAS, account-key, anonymous, or default-identity configuration. It supplies
the configured string or constructs Python `DefaultAzureCredential`, then passes
that credential to `BlobServiceClient`; its client context closes the service.
Dagster therefore composes explicit credentials or SDK discovery. This example
does not supply a Python identity object to DuckDB, which uses a different SDK.

DuckDB's Azure `credential_chain` delegates to Azure's native SDK and obtains
tokens at query time. Its documented chain choices include environment, CLI,
managed identity and workload identity. Scoped secrets support separate storage
accounts. The currently redirected [DuckDB Azure documentation](https://duckdb.org/docs/2.0/core_extensions/azure)
is for 2.0; newer providers described there are not assumed available in Assquack's
1.5+ deployment baseline. The relevant native implementation constructs C++ SDK
credentials in [the Azure client source](https://github.com/duckdb/duckdb-azure/blob/main/src/azure_storage_account_client.cpp).

Microsoft assigns Entra token acquisition and management to the
[Azure Identity library](https://learn.microsoft.com/en-us/azure/developer/cpp/sdk/authentication/overview).
Keeping the native chain leaves that responsibility with the SDK. This research
does not verify long-running export refresh against any deployed extension version.
An explicit SAS is a supplied expiring capability; Assquack currently reads it once
per export and cannot renew it during COPY. An environment change cannot replace
the SAS already registered for an active export. Its issuer/loader must supply a
valid replacement for later exports and sufficient lifetime for the current one.

## What the repository already does

[EnvironmentSasProvider](../../src/assquack/_exports/azure_credentials.py) reads
`AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_SAS_TOKEN` at use time.
[Azure authentication](../../src/assquack/_exports/azure.py) chooses SAS or the
native chain, registers a scoped temporary secret on the COPY connection and
cleans it up. Explicit chain selection bypasses SAS lookup. The
[writer](../../src/assquack/_exports/parquet.py) uses this context for Azure and
does not call it for local paths. The internal `AzureSasProvider` protocol supplies
only SAS credentials; it is not a general identity or token-refresh protocol.

The historical [MAD.Prefect EnvBlock source](../../.submodules/MAD.Prefect/mad_prefect/envblock.py)
needs a precise distinction: `from_env()` reads prefixed environment fields, or
loads a named Prefect block selected through `<PREFIX>_CREDENTIAL_BLOCK_NAME`.
It caches the returned instance. It does **not** itself populate the process
environment. A downstream bootstrap can translate its result into the variables
Assquack expects, before export, without adding Prefect to Assquack. The cached
block is not evidence of automatic rotation. No block instances were accessed.

## Alternatives and concrete limits

| Approach | Ownership and benefit | Practical limit |
| --- | --- | --- |
| Deployment/bootstrap populates environment | Caller retrieves and maps values; Assquack keeps a small contract. Works with Dagster, a script or external block loader. | Values belong to the process; changing one global pair per concurrent caller is unsafe. |
| Native DuckDB identity discovery | Caller provisions identity and permissions; native SDK acquires tokens. No Assquack secret-manager integration. | Identity availability and account authorization still need deployment configuration. |
| In-process retrieval with explicit injection | Caller resource/provider fetches credentials and supplies them without environment mutation. | Requires an agreed Assquack public boundary, error handling and lifetime semantics; a callback alone does not provide refresh. |

Multiple accounts do not necessarily mean multiple identities: one authorized
identity can access several fully qualified account destinations. In the current
implementation, leave the optional account restriction unset for that use case.
Different SAS credentials are the concrete limitation: the one fixed environment
pair cannot represent two concurrent accounts, or two independently scoped tokens
for the same account. Separate worker processes can provide separate environments.
If the product requires those callers in one process, consider destination-specific
environment-variable references first, or explicit per-destination sourcing when
environment injection is unsuitable. Neither option is an approved API here.

Temporary secret naming and URI scoping help routing and cleanup; they do not
turn a shared process into a caller-isolation boundary. Avoid solving concurrent
identity changes by temporarily overwriting global environment variables. This
is the main practical reason to revisit injection, rather than the existence of
an internal protocol by itself.

## EXT-02 disposition options

1. **Recommended: defer public injection.** Record external setup and native
   discovery as sufficient for the current use cases, preserve the internal seam,
   and revisit when simultaneous distinct credentials or direct secret retrieval
   is a demonstrated requirement.
2. **Reclassify as an intentional boundary**, if the owner explicitly chooses
   environment-only SAS sourcing for this release. An inaccessible private
   protocol is not a broken public promise unless public injection was promised.
3. **Retain as a required repair** only if a concrete supported caller must supply
   credentials through normal exports without process-environment changes. Then
   specify the narrow destination/lifetime requirement before designing a protocol.

No option was applied to the register. The agreed
`FileSystemDestination(base_uri, auth)` composition and authenticator ownership
remain intact. Credential sourcing can stay external without undoing that design.

## Related Docs

- [Exports](../exports.md): current behavior and agreed refactor direction.
- [Configuration](../configuration.md#adls-credentials): implemented Azure contract.
- [Scope decision](../journal/2026-09-08-mvp-scope.md#agreed-filesystem-destination-pattern-26-october-2026): approved composition.
- [EXT-02](../epics/01-mvp/06-extensibility-defects.md#ext-02-export-credential-provider-injection): proposal, unchanged by this research.

## Evidence limits

Public documentation and source inspection only; upstream branch links can change.
No credential files, configured blocks, customer systems or live cloud services
were accessed. No packages were installed and no code was executed against Azure.
Existing docs, index and code were left unchanged as requested.
