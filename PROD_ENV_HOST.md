# `prod-env-host` Guide

`prod-env-host` is the production deployment controller. Its only implemented
operation is currently `deploy-eu-squid`: it connects to the existing
`eu-server`, deploys Squid, and sequentially checks that it starts, serves HTTP
requests through the proxy, and blocks prohibited destinations.

This command does **not** deploy Outline or MTProxy, apply or reset the
firewall, connect to `ru-server`, or create test infrastructure. The complete
production rollout has not been implemented yet.

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

Required EU-stage settings:

| Variable | Value |
| --- | --- |
| `EU_SERVER_ADDRESS` | IPv4 or IPv6 address of `eu-server`, used by the controller for SSH and by the service configuration. |
| `RU_SERVER_ADDRESS` | IPv4 or IPv6 peer address for a future EU firewall rule. This value does not authorize the command to connect to RU. |
| `SSH_USER` | SSH account on `eu-server`. |
| `SSH_KEY_PATH` | Absolute path on the controller to the SSH key; do not put the key itself in `prod.env`. |
| `SSH_PORT` | SSH TCP port; defaults to `22`. |
| `EU_METADATA_ADDRESS`, `EU_DHCP_SERVER_ADDRESS` | IPv4 or IPv6 addresses of the EU network's metadata and DHCP services. |
| `EU_DNS_ADDRESSES`, `EU_NTP_ADDRESSES` | Non-empty JSON arrays of IPv4/IPv6 addresses for EU DNS and NTP services. |
| `EU_LOCAL_NETWORKS` | Non-empty JSON array of EU networks in strict CIDR notation. |

Server addresses are validated as IP addresses. `EU_SERVER_ADDRESS` must be
the SSH-reachable address assigned to `eu-server`; DNS names are not accepted.
The `EU_SERVER_ADDRESS` and `RU_SERVER_ADDRESS` names do not themselves
restrict the address range.

Lists must be written as JSON directly in the dotenv file, for example:

```dotenv
EU_DNS_ADDRESSES=["192.0.2.53","2001:db8::53"]
EU_NTP_ADDRESSES=["192.0.2.123"]
EU_LOCAL_NETWORKS=["192.0.2.0/24","2001:db8:1::/64"]
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
3. Settings that cannot be determined automatically, such as
   `RU_SERVER_ADDRESS`, or whose discovery failed, is ambiguous or found
   nothing (for example, no metadata endpoint), are
   requested from the operator explicitly, one by one, with the allowed form
   described in the prompt.
4. Discovered and entered values are validated with the same rules as the
   loader, shown to the operator for confirmation, and only then written to
   `config/prod.env` (mode `0600`, atomically). An existing file is never
   overwritten without explicit confirmation.
5. Discovery is read-only on `eu-server`, never prints secrets, and does not
   change the server or its firewall.

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
2. It then invokes the existing `eu-server-agent`, `eu-server-test-agent`,
   `ru-server-agent` and `ru-server-test-agent` functions in the same order
   as `test_env_host`. Each deployment or firewall action is followed by its
   matching test-agent check, and the run stops at the first failure with a
   nonzero exit code that identifies the failed step. Only the steps with a
   production equivalent run (see the exclusions in task 200: no GCP
   provisioning, IAP, test client, destructive checks or cleanup).
3. The sequence is implemented in stages: the EU stage first (tasks 213–242),
   then the RU stage after the RU decomposition (task 201). Until the RU stage
   exists, a run without arguments performs the EU stage only and reports
   that the RU stage is not implemented.
4. Single steps stay available as explicit operations (`configure`,
   `deploy-eu-squid`) for repeating or debugging one step.

### Current behavior

Until the full sequence above is implemented (task 245), a run without
arguments ensures a valid configuration (interactively on a terminal) and then
performs the EU stage, which currently deploys Squid and runs its checks:

```sh
.venv/bin/prod-env-host
```

Run `configure` or the Squid deployment alone by selecting the operation
explicitly:

```sh
.venv/bin/prod-env-host deploy-eu-squid
```

The command runs these steps in order and stops at the first failure:

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
