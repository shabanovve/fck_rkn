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
existing file. Fill in the values in the new file before running the command
again.

Required EU-stage settings:

| Variable | Value |
| --- | --- |
| `EU_SERVER_HOST` | IP address or DNS name used by the controller to connect to `eu-server` over SSH. |
| `EU_SERVER_ADDRESS` | IPv4 or IPv6 address of `eu-server`, used as a service address. |
| `RU_SERVER_ADDRESS` | IPv4 or IPv6 peer address for a future EU firewall rule. This value does not authorize the command to connect to RU. |
| `SSH_USER` | SSH account on `eu-server`. |
| `SSH_KEY_PATH` | Absolute path on the controller to the SSH key; do not put the key itself in `prod.env`. |
| `SSH_PORT` | SSH TCP port; defaults to `22`. |
| `EU_METADATA_ADDRESS`, `EU_DHCP_SERVER_ADDRESS` | IPv4 or IPv6 addresses of the EU network's metadata and DHCP services. |
| `EU_DNS_ADDRESSES`, `EU_NTP_ADDRESSES` | Non-empty JSON arrays of IPv4/IPv6 addresses for EU DNS and NTP services. |
| `EU_LOCAL_NETWORKS` | Non-empty JSON array of EU networks in strict CIDR notation. |

Server addresses are validated as IP addresses; `EU_SERVER_HOST` also accepts
a DNS name. The `EU_SERVER_ADDRESS` and `RU_SERVER_ADDRESS` names do not
themselves restrict the address range.

Lists must be written as JSON directly in the dotenv file, for example:

```dotenv
EU_DNS_ADDRESSES=["192.0.2.53","2001:db8::53"]
EU_NTP_ADDRESSES=["192.0.2.123"]
EU_LOCAL_NETWORKS=["192.0.2.0/24","2001:db8:1::/64"]
```

Replace every `SET_TO_*` placeholder with real settings for the selected
production network. The validator does not fill values automatically; on
error, it reports the names of invalid fields without displaying their
values. Settings are loaded from `config/prod.env`, not from the process
environment.

The schema currently requires all the settings listed above, although
`deploy-eu-squid` directly uses only `EU_SERVER_HOST` and the SSH settings.
The addresses, DNS/DHCP/NTP settings, and local networks are needed by later
production stages and are only validated at this point.

## Run

With no arguments, `prod-env-host` runs its current default operation,
`deploy-eu-squid`:

```sh
.venv/bin/prod-env-host
```

The operation can also be selected explicitly:

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

## Troubleshooting

- **Configuration error:** Check that `config/prod.env` is complete and that
  address values, CIDRs, and JSON arrays are valid.
- **SSH or transport failure:** Check `EU_SERVER_HOST`, the port, username,
  key permissions, network reachability, and `sudo` permissions.
- **Squid deployment failure:** Check Docker and the server's network access
  for downloading the image.
- **Health check failure:** Resolve the issue on the EU server and run the
  command again; later steps are not run automatically after a failure.

Do not pass key material or secrets in command arguments, logs, or the
repository. `config/prod.env` is for local settings and must not be committed.
