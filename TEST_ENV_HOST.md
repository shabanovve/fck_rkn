# Using `test_env_host`

`test_env_host` prepares the isolated GCP test VM `eu-server`. It validates
local settings, verifies Google Cloud access, creates or checks the scoped SSH
firewall rules, finds or creates the VM, waits for it to run, and checks SSH
readiness through IAP and OS Login.

The orchestrator ensures Cloud Router/Cloud NAT, provisions `eu-server`,
checks VM access, runs the initial `eu-server-agent` ping action, deploys
Squid, verifies Squid startup, and sends an HTTP request to `youtube.com`
through Squid. It then deploys Outline Server and runs the Outline health
check. Provisioning the router, NAT, VM, firewall rules, Squid, and Outline
changes GCP resources and can incur charges, including Cloud NAT and
automatically allocated NAT IP charges. Run it only in the `fck-rkn-test`
project when provisioning is intended.

The initial `eu-server-agent` action is also available directly as
`.venv/bin/eu-server-agent`. It runs one bounded `ping youtube.com` from
`eu-server` over IAP SSH. When called through the orchestrator, it runs only
after VM provisioning and IAP SSH readiness have succeeded.

To deploy Squid directly from the repository environment, run
`.venv/bin/eu-server-agent deploy-squid`. The action installs Ubuntu's
`docker.io` package if Docker is missing, then starts the `ubuntu/squid`
container with TCP port 3128 published on `eu-server`. Re-running the action
leaves it untouched if it is already running; a stopped container is replaced.
`test_env_host` runs this action automatically after the ping check.
Deployment output is streamed to the terminal as the remote commands run.

To stream the Squid container's live Docker logs independently, run
`.venv/bin/eu-server-agent logs-squid`. Press Ctrl-C to stop following logs.

To deploy Outline Server directly on `eu-server`, run
`.venv/bin/eu-server-agent deploy-outline`. The agent first builds an image
from the official stable image with `outline-ss-server` patched to accept
private target addresses (the stock server rejects them), so RU clients can
reach the private EU Squid endpoint through Outline. The patch removes the
server's private-network protection; the risk is accepted because production
also sends RU requests through the tunnel to the private Squid address. The
official installer then runs that image and configures
Outline with the VM's private IP and fixed management/access-key ports. Its
secret-bearing installer output is suppressed; generated state remains in the
protected `/opt/fck-rkn-outline` directory on the VM. The orchestrator does
run this action automatically after the Squid checks. If the named container
is already running the expected image, it is left unchanged. A mismatched
existing container is not replaced.

Outline uses Shadowsocks as an application-layer proxy. It does not create an
L3 VPN interface, routes, or a separate tunnel address range. Clients on
another VM must connect to the private Outline endpoint, with VPC firewall
access configured separately.

After deploying Outline, run `.venv/bin/eu-server-test-agent outline` to
verify the expected container is running, the secret management API returns
an authorized access-key list, and the access-key TCP endpoint is listening.
The check does not print the API URL or any access keys. `test_env_host` runs
this check automatically after successful Outline deployment.

For operator instructions to create or retrieve an Outline user on
`eu-server` and securely handle its access URL, see
[Creating an Outline user on `eu-server`](EU_SERVER_OUTLINE_USER_GUIDE.md).
`test_env_host` creates or reuses its named test user after the Outline
health check, passes the URL in memory to `eu-server-test-agent`, and never
displays it. The test-agent starts the pinned
`ghcr.io/shadowsocks/sslocal-rust:v1.25.0` client temporarily on `eu-server`
and makes an HTTP request to `youtube.com` through the resulting local SOCKS
listener. The temporary client container is removed after the check.

## Prerequisites

- A configured GCP project named `fck-rkn-test`, with billing enabled.
- The Compute Engine and IAP APIs enabled in that project.
- A VPC network and subnet in the VM's region. The orchestrator creates or
  verifies a dedicated Cloud Router and public Cloud NAT for that subnet.
  Test VMs use NAT for outbound Internet access and must not have external IPs.
- Google Cloud CLI (`gcloud`) installed.
- Docker on the orchestrator host. The patched Outline Server image is built
  once there and loaded onto `eu-server` and then `ru-server`; if the image
  already exists on the host, it is reused and not rebuilt.
- The operator signed in to the CLI, and Application Default Credentials
  configured for the Python client libraries.
- Permissions to inspect the project, create Compute Engine instances, manage
  the scoped VPC firewall rules, create/read regional Cloud Routers, use the
  selected subnet, establish IAP TCP forwarding, and log in through OS Login.

Sign in to both the CLI and ADC with the intended Google account:

```sh
gcloud auth login
gcloud auth application-default login
gcloud config set project fck-rkn-test
gcloud auth list
```

Enable the required APIs if they are not already enabled:

```sh
gcloud services enable compute.googleapis.com iap.googleapis.com \
  --project=fck-rkn-test
```

For required IAM roles and least-privilege guidance, see
[Development Guidelines](DEVELOPMENT.md#ssh-through-iap).

## Local configuration

Create the ignored local configuration from the tracked template if it does
not already exist:

```sh
cp -n config/test.env.example config/test.env
```

Keep `GCP_PROJECT_ID` set to `fck-rkn-test`; the program rejects other
projects. SSH/TCP 22 is permitted from any source by the scoped test firewall
rule; IAP remains the recommended operator connection method.

The remaining template defaults specify the VM zone, machine type, Ubuntu
image family, VPC/subnet, and SSH network tag/firewall rule names. Local
configuration is ignored by Git and must not be committed or shared.

`test_env_host` is the only place that uses GCP, IAP and `gcloud`. It hands
the agents a `GcloudIapRunner` and the VM private addresses; the agents
themselves are platform-independent and in production run over SSH with
`config/agents.env` (see [Development Guidelines](DEVELOPMENT.md#platform-independence-and-firewall)).

## Run

From the repository root, run either the preferred package entry point:

```sh
.venv/bin/python -m fck_rkn
```

or the compatibility launcher:

```sh
.venv/bin/python test_env_host.py
```

To invoke the ping action directly after provisioning:

```sh
.venv/bin/eu-server-agent
```

The CLI emits timestamped progress logs to the console, including config
validation, project access, firewall-rule checks, VM lookup/creation, VM
readiness, and IAP SSH readiness. It intentionally does not print local
configuration values or capture and display the output of SSH retry attempts.

The ping agent prints the captured remote command output. It exits with status
`0` only when both the SSH command and ping succeed; SSH failures, ping
failures, and command timeouts return a nonzero status. The orchestrator also
returns nonzero if the agent action fails.

The `deploy-squid` action exits with status `0` only when the remote Docker
deployment command succeeds; SSH failures, Docker/package errors, and command
timeouts return nonzero.

After deployment, run `.venv/bin/eu-server-test-agent` to verify that the
Squid container is running and that TCP port 3128 accepts local connections on
`eu-server`. `test_env_host` runs this check after Squid deployment.

To check that Squid denies Russian domains (`.ru`, `.su`, `.рф`) and any
destination given as an IP address literal, run
`eu-server-test-agent squid-block`. `test_env_host` runs it after the Squid
HTTP check and fails with exit status 62 if any request is not denied (403).

To test an HTTP GET to `youtube.com` through Squid, run
`.venv/bin/eu-server-test-agent http`. An HTTP status from 200 through 399 is
considered successful; the action prints the returned status and exits
nonzero for a failed request or any other status. `test_env_host` runs this
check after the Squid startup check.

## Firewall reset at the start of a run

Each full run starts from a clean state. As soon as `eu-server` is running and
SSH-ready, and before any agent step on it, `test_env_host` removes the
project firewall left by an earlier run (nftables table, systemd unit and
saved rules) and verifies that nothing remains (exit codes 58 and 59). It does
the same on `ru-server` before the first RU agent step (exit codes 60 and 61).
Firewall rules are installed again only after the whole infrastructure,
including `test-client`, is configured and verified.

After installing each server firewall, the matching agent installs and runs
the shared daily RKN blacklist updater and enables its systemd timer. The
orchestrator runs the shared `firewall-blacklist` test-agent check on both
servers; it verifies that the timer is enabled and active and that both active
crawler address sets are populated. The RU firewall allows updater HTTPS
traffic only to GitHub's published API and web ranges. Exit codes 65 and 66
indicate a failed updater check on `eu-server` and `ru-server`, respectively.

## Repeating a single check

Before the firewall steps, `test_env_host` saves their inputs (firewall
configurations, a crawler-source address and the test Outline keys) to
`config/last_run/`, which is git-ignored and readable by you only. With the
VMs still running, repeat one check from the latest run by adding
`--last-run` to a test agent action instead of piping input through stdin:

```
.venv/bin/ru-server-test-agent firewall-policy --last-run
.venv/bin/ru-server-test-agent firewall-vpn-client --last-run
.venv/bin/ru-server-test-agent firewall-crawler-source --last-run
.venv/bin/eu-server-test-agent firewall --last-run
```

`outline-client --last-run` uses the saved RU probe key. Actions that need no
input (for example `firewall-rollback`) run the same way with or without the
flag. Run `test-env-host` first; without a saved run the agent reports which
input is missing.

## Behavior and safety

- If `eu-server` is absent, the tool creates it without an external IP.
- If it already exists, the tool reuses it only when its configuration meets
  the expected private-network, OS Login, and SSH-tag settings. It does not
  silently modify a mismatched VM.
- The tool creates or verifies VM-scoped SSH firewall rules that allow
  TCP/22 from any source. It migrates its previously managed IAP-only rule;
  unrelated conflicting rules cause provisioning to stop.
- The tool waits for the VM to enter `RUNNING`, then runs a short
  `gcloud compute ssh --tunnel-through-iap` command to verify OS Login SSH.
- Cloud NAT is created or verified before VM provisioning and provides
  outbound Internet connectivity, including the current `ping youtube.com`
  action. A same-named router with any conflicting configuration causes
  provisioning to stop; it is not modified. NAT and allocated public IP
  addresses can incur charges.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Configuration syntax error | The CLI prints the filename and line number, not the contents. Fix that line to use `KEY=value` syntax. |
| ADC authentication or project access failure | Repeat `gcloud auth application-default login`, confirm the active account and project, and verify the APIs and IAM permissions. |
| No active `gcloud` account | Run `gcloud auth login` and confirm the intended account is active. |
| Firewall rule conflicts | Review the named rule in GCP. The tool will not overwrite a rule whose settings differ from the required configuration. |
| Cloud Router/NAT conflicts | Review the dedicated router in the VM region. The tool will not overwrite a router whose NAT settings differ from the required configuration. |
| VM configuration mismatch | Review the existing `eu-server`; the tool refuses to silently alter it. |
| VM does not reach `RUNNING` | Check Compute Engine operation errors, quota, billing, selected zone, image, machine type, and subnet. |
| IAP SSH readiness times out | Confirm IAP TCP forwarding permission, OS Login access, the enabled IAP API, and the VM's scoped SSH firewall rule. |

The process returns status `0` on success, `2` for invalid or unreadable
configuration, `3` for authentication failures, `4` for project-access
failures, `5` for provisioning or readiness failures, and `6` if the remote
ping agent fails, `7` if Squid deployment fails, and `8` if the Squid startup
test fails, `9` if the HTTP-through-Squid test fails, `10` if Outline
deployment fails, and `11` if the Outline health check fails
(container not running, API unavailable, or VPN port closed). Later RU
steps use `12`–`19`; the tunnel-loss test returns `20` if the tunnel cannot be
stopped, `21` if traffic is not blocked while it is down, `22` if recovery
fails, and `23` if the forward does not resume after recovery.

## Manual VM shutdown

Run `.venv/bin/test-env-host shutdown-vms` to stop every `RUNNING` VM in all
zones of the isolated `fck-rkn-test` project. The command waits for each stop
operation to finish. VMs in other states are left unchanged, and no VMs,
disks, or network resources are deleted. Normal provisioning never stops VMs.
Exit code 14 means the shutdown failed.
