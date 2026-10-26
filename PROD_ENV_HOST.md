# `prod-env-host` Guide

`prod-env-host` is the production deployment controller. The current
implementation runs the EU Phase 2A service stage without applying a firewall.
The RU stage and full rollout remain pending: after RU services pass their
checks in Phase 2B, the controller must apply both firewalls and repeat the
approved checks on both servers (tasks 227–230 and 301–328).

The explicit `deploy-eu-squid` operation connects to the existing `eu-server`,
deploys Squid, and checks that it starts, serves HTTP requests through the
proxy, and blocks prohibited destinations. Production does not reset the
firewall, connect to `ru-server`, or create test infrastructure.

## Requirements

- A controller with Python 3.10 or later, `ssh`, and a project checkout.
- An existing `eu-server` accessible from the controller over SSH.
- An SSH account permitted to run deployment commands, including the
  necessary `sudo` commands.
- An SSH key authorized for that account. Docker and network access for
  downloading the Squid image and running checks must be available on the
  server.

## Installation

Run these commands from the project checkout root. An editable installation
ensures that `prod-env-host` finds `config/prod.env` in this checkout.
Production dependencies do not include the GCP extra:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/prod-env-host --help
```

For the local test environment, install from `requirements.txt`, which also
installs the `test-env` extra.

## Configure `config/prod.env`

On first run, the command atomically creates a missing `config/prod.env` from
`config/prod.env.example` with permissions `0600`. It does not overwrite an
existing file. Run `prod-env-host configure` to build the file interactively
(see [Interactive configuration](#interactive-configuration-required-behavior));
Running `prod-env-host` without arguments starts the same flow automatically
when `config/prod.env` is missing or invalid on a terminal, then continues
with the deployment.
Alternatively, fill in the template copy by hand.

Required Phase 2A settings:

| Variable | Value |
| --- | --- |
| `EU_SERVER_ADDRESS` | IPv4 or IPv6 address of `eu-server`, used by the controller for SSH and by the service configuration. |
| `SSH_USER` | SSH account on `eu-server`. |
| `SSH_KEY_PATH` | Absolute path on the controller to the SSH key; do not put the key itself in `prod.env`. |
| `SSH_PORT` | SSH TCP port; defaults to `22`. |
| `EU_METADATA_ADDRESS`, `EU_DHCP_SERVER_ADDRESS` | IPv4 or IPv6 addresses of the EU network's metadata and DHCP services. |
| `EU_DNS_ADDRESSES`, `EU_NTP_ADDRESSES` | Non-empty JSON arrays of IPv4/IPv6 addresses for EU DNS and NTP services. |
| `EU_LOCAL_NETWORKS` | Non-empty JSON array of EU networks in strict CIDR notation. |

Required Phase 2B settings (optional for Phase 2A):

| Variable | Value |
| --- | --- |
| `RU_SERVER_ADDRESS` | IPv4 or IPv6 address used for RU SSH and service/firewall configuration. |
| `RU_METADATA_ADDRESS`, `RU_DHCP_SERVER_ADDRESS` | IPv4 or IPv6 addresses of RU's metadata and DHCP services. |
| `RU_DNS_ADDRESSES`, `RU_NTP_ADDRESSES` | Non-empty JSON arrays of RU DNS and NTP service addresses. |
| `RU_LOCAL_NETWORKS` | Non-empty JSON array of RU local networks in strict CIDR notation. |
| `RU_PROVIDER_NETWORKS` | Non-empty JSON array of production provider egress CIDRs, validated against the crawler blacklist. |

Server addresses are validated as IP addresses. `EU_SERVER_ADDRESS` must be
the SSH-reachable address assigned to `eu-server`; DNS names are not accepted.
RU values are optional for Phase 2A. Phase 2B requires the RU server address,
host service values and provider network ranges described in task 301. The
same `RU_SERVER_ADDRESS` is used for SSH and RU service/firewall configuration;
do not add a separate RU SSH-host or private-address setting.

Lists must be written as JSON directly in the dotenv file, for example:

```dotenv
EU_DNS_ADDRESSES=["192.0.2.53","2001:db8::53"]
EU_NTP_ADDRESSES=["192.0.2.123"]
EU_LOCAL_NETWORKS=["192.0.2.0/24","2001:db8:1::/64"]
RU_DNS_ADDRESSES=["192.0.2.54"]
RU_PROVIDER_NETWORKS=["198.51.100.0/24"]
```

Replace every `SET_TO_*` placeholder with real settings for the selected
production network. The validator does not fill values automatically.
Settings are loaded from `config/prod.env`, not from the process environment.

### Configuration validation errors

When configuration validation fails, `prod-env-host` must report each
actionable problem separately, including the setting name and why it is
invalid (for example, missing value, unreplaced placeholder, invalid IP/CIDR,
or malformed JSON list). Dotenv syntax errors must include the file and line
number. Diagnostics must not print the supplied value or other secret
configuration contents. A generic message such as “Could not load valid
production configuration” alone is insufficient. Validation failures are
reported as a field-by-field list; dotenv syntax errors identify the file and
line.

### Interactive configuration (required behavior)

`prod-env-host configure` generates `config/prod.env` interactively instead of
requiring it to be filled in by hand. The settings table above
remains the schema and validation reference; manual edits stay valid because
the loader checks the final file either way:

1. The step prompts for the `eu-server` address, and for the SSH settings
   needed to connect (`SSH_USER`, `SSH_KEY_PATH`, `SSH_PORT`), which cannot be
   discovered before a connection exists.
2. After the SSH connection is established, it automatically determines every
   other setting that can be read from `eu-server` (for example, local
   networks, DNS resolvers, DHCP server, NTP servers and the metadata
   endpoint, probed where the provider offers one).
3. It connects to `ru-server` using the entered `RU_SERVER_ADDRESS` and shared
   SSH credentials, discovering read-only RU host settings (`RU_METADATA_ADDRESS`,
   `RU_DNS_ADDRESSES`, `RU_DHCP_SERVER_ADDRESS`, `RU_NTP_ADDRESSES`,
   `RU_LOCAL_NETWORKS`). Settings that cannot be determined automatically or whose
   discovery failed (including `RU_PROVIDER_NETWORKS`) are requested from the
   operator explicitly, one by one, with the allowed form described in the
   prompt.
4. Discovered and entered values are validated with the same rules as the
   loader, shown to the operator for confirmation, and only then written to
   `config/prod.env` (mode `0600`, atomically). An existing file is never
   overwritten without explicit confirmation.
5. Discovery is read-only on both `eu-server` and `ru-server`, never prints secrets,
   and does not change either server or its firewall.

The schema currently requires all the settings listed above, although
`deploy-eu-squid` directly uses only `EU_SERVER_ADDRESS` and the SSH settings.
The addresses, DNS/DHCP/NTP settings, and local networks are needed by later
production stages and are only validated at this point.

## Run

### Target behavior: full deployment without arguments (required)

`prod-env-host` is the production counterpart of `test_env_host`. Run without
arguments, it deploys and verifies the system automatically, with no further
operator commands:

```sh
.venv/bin/prod-env-host
```

1. If `config/prod.env` is missing or invalid and a terminal is available, it
   first runs the [interactive configuration](#interactive-configuration-required-behavior).
   Without a terminal it exits with code `2` and the validation diagnostics.
2. It runs Phase 2A followed by Phase 2B in dependency order, stopping at the
   first failure with a nonzero exit code identifying the failed step. It
   omits test-only actions (GCP provisioning, IAP, test client, destructive
   checks and cleanup).
3. Phase 2A deploys and checks EU services only. Phase 2B deploys and checks
   RU services. After both server stages pass, it applies both guest firewalls
   without resetting existing policies, then repeats the approved read-only
   service/forwarding checks and verifies both active firewall policies and
   blacklist updaters. RU decomposition is complete (task 201); the
   implementation and revised EU firewall sequence are pending tasks 226–230
   and 301–328. Until then, the current command runs only the existing EU
   stage and reports that RU is not implemented.
4. Single steps stay available as explicit operations (`configure`,
   `deploy-eu-squid`, `build-outline-artifact`) for repeating or debugging
   one step.

### Current behavior

A run without arguments performs the whole EU stage and stops at the first
failure, naming the step. Steps, in order:

1. Squid deployment, startup check, HTTP check and blocking check.
2. Outline image load (verified artifact), Outline deployment and health check.
3. MTProxy deployment and check.
4. Creation or reuse of the dedicated EU Outline key for the RU stage, kept in
   the operator-local secret store.
5. No firewall operation runs in Phase 2A. The current implementation stops
   after the EU service checks and RU-stage key are ready; RU deployment and
   the post-firewall sequence remain pending.

Not run, by design: either firewall application during Phase 2A, the firewall
rollback simulation, the invasive probe-based firewall check, test-run state,
and every GCP or test-client operation. On success the EU service stage
completes and the RU stage is reported as not implemented pending tasks
301–328. When implemented, both firewalls are applied only after RU services
pass their pre-firewall checks. The Outline load
step fails closed until an approved digest is committed (see "Outline image
artifact"); use `deploy-eu-squid` to run the Squid steps alone. The read-only
firewall check is also available as `eu-server-test-agent firewall-readonly`
(config on stdin).

Run `configure` or the Squid deployment alone by selecting the operation
explicitly:

```sh
.venv/bin/prod-env-host deploy-eu-squid
```

`deploy-eu-squid` runs these steps in order and stops at the first failure:

1. Deploys Squid on `eu-server`.
2. Checks the running container and its local TCP listener.
3. Checks an HTTP request through Squid.
4. Checks that prohibited destinations are blocked.

On success, it returns exit code `0`. A production configuration error returns
code `2`; an SSH command or check failure returns a nonzero code. Logs identify
the failed step. The command does not remove the existing firewall or reset
configuration before running. If an existing Squid container is running with
a different configuration, the deployment script replaces it to apply the
current access policy.

## Outline image artifact

Production never builds or pulls the patched Outline Server image as a side
effect of a deployment. It consumes a reviewed artifact:

- **Source:** an archive built locally by the operator from the pinned
  `outline_shadowbox.Dockerfile` (`outline-ss-server` tag `v1.7.3` on the
  `shadowbox:stable` base). No registry or publishing service is used.
- **Identity:** the SHA-256 digest of the gzip archive. The approved digest
  is the single lowercase hex line in the tracked file
  `artifacts/outline-image.sha256`. The archive itself lives untracked at
  `config/artifacts/outline-image.tar.gz`. A tag name alone is never trusted.
- **Provenance:** the reviewed change that updates the digest file records
  the Dockerfile revision, `outline-ss-server` version and base image used.
  The base image tag is mutable, so a rebuild may produce a different digest;
  the approved archive, not a rebuild, is what gets deployed.
- **Verification:** before any transfer, the archive digest is computed and
  compared with the approved digest. A missing, empty or malformed digest
  file, a missing archive, or a mismatch stops the deployment.
- **Update policy:** build a new archive with
  `prod-env-host build-outline-artifact`, review it, and commit its digest in
  `artifacts/outline-image.sha256` through a normal reviewed change. Until a
  digest is committed, production Outline deployment fails closed.

## Secret storage for the RU stage

The dedicated EU Outline key for the later RU stage
(`ru-server-feasibility-probe`) is created or reused on `eu-server` and kept
only in the operator-local store: a `0600` file inside a `0700` directory,
`$XDG_CONFIG_HOME/fck_rkn/secrets/` (default `~/.config/fck_rkn/secrets/`),
outside the repository. It never appears in `prod.env`, logs, argv or test
run state. A run reuses the key on the server and refreshes the local copy
atomically; the server is authoritative. Reading a copy with permissions
broader than `0600` is refused. Access is limited to the operator account;
delete the file to revoke the local copy.

## Troubleshooting

- **Configuration error:** Use the field-specific validation reason and line
  number, when applicable, to correct `config/prod.env`; do not include
  secrets when sharing diagnostics.
- **SSH or transport failure:** Check `EU_SERVER_ADDRESS`, the port, username,
  key permissions, network reachability, and `sudo` permissions.
- **Squid deployment failure:** Check Docker and the server's network access
  for downloading the image.
- **Health check failure:** Resolve the issue on the EU server and run the
  command again; later steps are not run automatically after a failure.

Do not pass key material or secrets in command arguments, logs, or the
repository. `config/prod.env` is for local settings and must not be committed.
