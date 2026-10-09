# Infisical Secret Source Plugin for Hermes Agent

Resolves environment variables from an [Infisical](https://infisical.com)
secret store (US/EU cloud or self-hosted) at Hermes startup, so provider
credentials never live in plaintext in `~/.hermes/.env`.

Registers two secret sources:

| Source | Shape | Purpose |
|---|---|---|
| `infisical` | mapped | Bind env vars to explicit `inf://` references (wins conflicts) |
| `infisical_bulk` | bulk | Inject a whole project/environment/folder (yields conflicts) |

The plugin is strictly **read-only** and runs only at process startup, per
the Hermes secret-source contract.

## Requirements

- Hermes Agent with secret-source plugin support (API version 1) and
  `agent.secret_sources.base.get_source_environment` (introduced in Hermes
  commit `6ab390a4764050bfed7f6ecb9d88c8e1d6377547`, also pinned for tests).
  API version 1 alone is not sufficient on older builds; upgrade Hermes
  rather than falling back to process-wide credential reads.
- An Infisical server exposing the v4 secrets API (Infisical Cloud, or a
  recent self-hosted release).
- No extra Python packages: the plugin uses `requests`, which Hermes ships.

## Install

```bash
git clone <this-repo> ~/.hermes/plugins/infisical
```

Then enable plugins and the sources in your Hermes config.

## Configuration

```yaml
plugins:
  enabled: [infisical]

secrets:
  sources: [infisical, infisical_bulk]

  infisical:
    enabled: true                              # master switch
    host_url: https://us.infisical.com         # or EU / self-hosted URL
    organization: ""                           # optional org ID, validation only
    timeout_seconds: 120                       # wall-clock fetch budget
    override_existing: true                    # mapped bindings beat .env values
    auth:
      method: agent                            # agent | universal-auth | token
      agent_token_path: ~/.infisical/agent-token
      # for method: universal-auth —
      client_id_path: /etc/infisical/client-id
      client_secret_path: /etc/infisical/client-secret
    env:
      OPENAI_API_KEY: inf://my-project/prod/backend/OPENAI_API_KEY
      DB_PASSWORD: inf://my-project/prod/db/pg-creds?type=dynamic&field=password&ttl=8h

  infisical_bulk:
    enabled: true
    host_url: https://us.infisical.com
    organization: ""
    timeout_seconds: 120
    override_existing: false                   # bulk yields to everything else
    auth:
      method: agent
      agent_token_path: ~/.infisical/agent-token
    project: my-project                        # name, slug, or project ID
    environment: prod                          # environment slug
    secret_path: /backend                      # folder to inject from
    recursive: false                           # include nested folders
    include_imports: true                      # include imported secrets
```

Use YAML anchors to share the `auth`/`host_url` block between the two
sections.

### Reference format (`inf://`)

```
inf://<project>/<environment>[/<folder>...]/<key>[?options]
```

- `<project>` — project **name, slug, or ID**. Names/slugs are resolved via
  the API once per process; IDs skip that call. Percent-encode spaces in
  display names (`My%20Project`).
- `<environment>` — environment slug (`dev`, `staging`, `prod`, …).
- Folders are optional; without them the key is read from `/`.
- Options: `type=dynamic` (create a dynamic-secret lease), `field=<name>`
  (which field of the generated credentials to inject — required when the
  payload has more than one), `ttl=<duration>` (lease TTL, e.g. `8h`).

### Authentication methods

- **`agent`** (default): reads the access token the local
  [Infisical Agent](https://infisical.com/docs/integrations/platforms/infisical-agent)
  writes to its file *sink*. Point `agent_token_path` at the `path` you
  configured under `sinks:` in the agent's config.
- **`universal-auth`**: a
  [Machine Identity](https://infisical.com/docs/documentation/platform/identities/universal-auth)
  client ID/secret, taken from the `INFISICAL_CLIENT_ID` /
  `INFISICAL_CLIENT_SECRET` env vars or from the files named by
  `client_id_path` / `client_secret_path`. The plugin logs in at startup and
  holds the short-lived token in memory only.
- **`token`**: a static access token from the `INFISICAL_TOKEN` env var.

`INFISICAL_TOKEN`, `INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET` are
protected: no secret source (including this one) may overwrite them.

#### Scoped bootstrap credentials

Both `infisical` and `infisical_bulk` read these bootstrap variables through
Hermes' `get_source_environment()`. During a scoped fetch, including the
registry's timeout worker, that view is the environment supplied for the
profile being hydrated, not the launch process's `os.environ`.

An empty scoped view, a missing variable, or a whitespace-only value does
**not** fall back to launch-process credentials. For `token`, this reports
`NOT_CONFIGURED`. For `universal-auth`, a missing or empty credential still
uses its configured `client_id_path` or `client_secret_path`; without a
usable file it reports `NOT_CONFIGURED` before making a login request.
Nonempty environment values take precedence over credential files, and
surrounding whitespace is stripped as before.

When no scoped view is installed, Hermes' helper returns the process
environment, preserving ordinary single-profile startup behavior. Supply
bootstrap credentials in the target profile's environment or use explicit
credential files. The `agent` token sink and path expansion are unchanged;
this fix scopes environment-based credentials only.

### Limitations

- **Dynamic-secret leases are created once, at startup.** The Hermes
  contract has no runtime hook, so leases are never renewed — choose a
  `ttl` that outlives your agent session.
- **No write-back.** Reading only; creating or updating secrets is out of
  scope by design.
- The v4 list endpoint returns a folder in one response (no pagination);
  very large folders are bounded by the fetch timeout budget.

## Verification checklist (live end-to-end)

1. Install the plugin (above) and enable it in the Hermes config.
2. Create an Infisical project with a test secret, e.g.
   `prod` → `/backend/HERMES_E2E_CHECK`.
3. Configure one auth method (start the Infisical Agent, or export
   `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET`).
4. Map it: `env: {HERMES_E2E_CHECK: inf://<project>/prod/backend/HERMES_E2E_CHECK}`.
5. Start Hermes with `HERMES_PLUGINS_DEBUG=1` and confirm the plugin loads
   and `HERMES_E2E_CHECK` is present in the agent's environment.
6. Break the token on purpose and confirm startup continues with a clear
   one-line `infisical: auth_failed: ...` warning.

## Development

```bash
scripts/fetch-hermes.sh          # pinned hermes-agent checkout into .deps/
pip install -r requirements-dev.txt
python -m pytest tests/ --cov=infisical_plugin --cov-fail-under=80
```

Error messages follow one shape everywhere:
`infisical: <error_kind>: <actionable message>` — and never contain secret
values or tokens.
