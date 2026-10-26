# Development Guidelines

## Language

- Write all project content in English. This applies to source code, comments,
  docstrings, tests, documentation, configuration examples, commit messages,
  and user-facing text.
- Use clear, descriptive names and consistent formatting.

## Virtual Machines

- Use Ubuntu as the operating system for all project virtual machines.

## Git-tracked File Safety

- Except for the documented address below, `README.md` and `DEVELOPMENT.md`
  must not contain any concrete IPv4 or IPv6 addresses or CIDR ranges,
  including public service ranges such as the IAP TCP forwarding source
  range. Use descriptive placeholders there.
- `DEVELOPMENT_PLAN.md` and tests may use IP addresses, but only
  documentation and test ranges (reserved for documentation or benchmarking),
  never the addresses of test or production virtual machines or of real
  Internet hosts (except the documented address below).
- The public address `1.1.1.1` is the documented "other host" used by the
  firewall checks. It is the one real Internet address that may appear in
  source, tests and documentation.
- If code needs the IP address of a test or production environment (a VM,
  a subnet or similar), it takes it from that environment's configuration
  (`config/test.env`, `config/agents.env`) or resolves it at runtime from the
  environment. Such addresses are never written in source, tests or
  documentation.
- Files tracked by Git must never contain credentials or credential material,
  including passwords, private keys, access tokens, ADC files, or service
  account keys. Use placeholders in examples and keep real credentials in an
  approved local or managed secret store.
- Do not put credentials into source, tests, documentation, fixtures, or
  commit messages. Test with injected settings or documentation and test
  address ranges.

## Environments

- Name the test environment `fck-rkn-test`.
- Name the production environment `fck_rkn`.
- Keep test and production resources, credentials, and configuration
  separated.
- Divide development and deployment into two phases:
  1. **Test infrastructure:** build the infrastructure, deploy traffic
     filtering, and develop and verify changes only in the isolated test
     environment using `test_env_host`.
  2. **Production installation:** create production installation tooling for
     `eu-server` first, then `ru-server`, only after the relevant behavior
     has been developed and verified in the test environment. Implement and
     verify the production deployment entry point for EU before extending it
     to deploy RU; the first stage must not configure or change `ru-server`.
- The analysis of `test_env_host` and its dependency path is recorded in
  `DEVELOPMENT_PLAN.md` (tasks 88, 200 and 201). Keep it current when production
  requirements change. Record how test and production responsibilities
  differ; identify and justify every setting in `config/prod.env` and
  `config/prod.env.example`; and decide how
  `prod_env_host` will invoke and sequence `ru-server-agent`,
  `ru-server-test-agent`, `eu-server-agent`, and `eu-server-test-agent`.
  Include each agent's configuration, runner, side effects, exit status, and
  failure behavior in the analysis. Do not assume that the test orchestrator
  can be reused unchanged: test-only GCP provisioning, IAP access, test
  clients, firewall resets, and test-environment cleanup must remain out of
  production installation unless explicitly justified and approved.
- Keep production decisions in the development plan. The analysis is complete;
  it does not by itself establish that a particular implementation or
  configuration file exists.
- Implement production deployment in two ordered increments: first,
  `prod_env_host` deploys and verifies EU services only; this Phase 2A must not
  run RU deployment actions or modify RU state. Phase 2B then deploys and
  verifies RU services. Do not apply either production firewall until both
  servers' services pass their pre-firewall checks. Then apply both firewall
  policies and repeat the approved read-only service/forwarding checks on
  both servers, along with read-only policy and updater checks. Never reset an
  existing production firewall or run destructive/test-client checks against
  production. Keep the RU peer address needed for EU policy separate from
  permission to deploy RU. The EU-generated Outline access URL needed by RU is
  stored and retrieved through the approved operator-local `LocalSecretStore`;
  never place it in `prod.env`, command arguments, logs, or an unprotected
  file.
- Every GCP test VM must have outbound Internet access through Cloud NAT and
  must not have an external IP address. Do not use VM external IPs as an
  outbound-access substitute. Configure Cloud NAT for the VM's subnet and
  region, and verify outbound access through NAT as part of the relevant test
  workflow.
- Re-running Squid deployment must leave an already-running Squid container
  untouched; replace it only when it is not running or its access policy
  (the mounted `squid.conf`) has changed.
- Squid must deny, for both plain HTTP and `CONNECT`, every request whose
  destination is a Russian domain (the `.ru`, `.su` and `.рф`/`xn--p1ai` TLDs
  and their subdomains) and every request whose destination is an IP address
  literal (IPv4 or IPv6, with or without a port). Only domain names outside
  the Russian TLDs are allowed. Blocking is done in Squid, not in the firewall.
- Re-running Outline Server deployment must leave an already-running,
  correctly configured Outline container untouched. Refuse to replace an
  existing Outline container with unexpected configuration.
- The EU Outline Server image must be rebuilt from the official image with
  `outline-ss-server` patched to accept private target addresses, because RU
  requests reach the EU Squid through the tunnel at its private address. The
  patch removes the server's protection against requests to private networks;
  this risk is accepted, including for production.
- Provide a manual `test-env-host shutdown-vms` action that validates access
  to the isolated `fck-rkn-test` project, finds running Compute Engine VMs
  across all zones, stops them, and waits for the stop operations to complete.
  It must not delete VMs or their disks, network resources, or firewall rules,
  and must not run as part of the normal provisioning workflow.
- The repository's `config/` directory may contain only the tracked
  templates `config/test.env.example`, `config/agents.env.example`, and,
  when defined by the Phase 2 analysis, `config/prod.env.example`. Local
  configuration files (`config/test.env`, `config/agents.env`, and the
  future `config/prod.env`), generated files (including `config/last_run/`),
  credentials, and any other files under `config/` must not be added to the
  repository.
- Keep real environment values out of the example; use placeholders there.

## VPN User Provisioning

- Provide scripts to create VPN users or access keys for each Outline Server.
- `eu-server-agent` and `ru-server-agent` must run the appropriate user
  provisioning script after Outline Server is deployed and running, before
  the corresponding client connectivity tests.
- Keep generated access keys and other credentials in a protected secret
  store; never commit them or write them to unredacted logs.

## Infrastructure Build Order and Verification

- The infrastructure is configured by `eu-server-agent` and
  `ru-server-agent`. Every change made by `ru-server-agent` is verified by
  `ru-server-test-agent`, and every change made by `eu-server-agent` by
  `eu-server-test-agent`; `test-client-agent` additionally verifies the
  result from the client side.
- In the test environment, build in this order: `eu-server` first, then
  `ru-server`, and `test-client` last. Each stage must pass its checks before
  the next one starts.
- In the test environment, firewall rules are installed only after the whole
  infrastructure is configured and verified (all three stages above). In
  production, apply neither firewall until both servers' services pass their
  checks, then repeat the approved read-only checks after applying both. In
  the test environment,
  `test_env_host` removes the project firewall rules left by an earlier run
  on `eu-server` and `ru-server` as soon as the VMs are running and
  SSH-ready, before the first agent step of every full run, so the build is
  never affected by an old policy. This reset is a `test_env_host` step only;
  production agents never remove rules automatically.
- Firewall checks reuse the code of the test agents (`eu-server-test-agent`,
  `ru-server-test-agent`, `test-client-agent`) instead of duplicating it.

## Platform Independence and Firewall

- Keep the production deployment independent of any specific cloud provider.
  GCP is the platform for the test environment only.
- GCP, IAP and `gcloud` belong only to the test layer (`fck_rkn/gcp/` and
  `orchestration/`). Agents under `fck_rkn/agents/` must not import them or
  name a cloud provider; a test enforces this. Remote scripts under
  `remote/scripts/` must not reference a cloud provider either.
- Agents run commands through the `RemoteRunner` protocol
  (`fck_rkn/remote/`). The test environment selects `GcloudIapRunner`
  (`fck_rkn/gcp/runner.py`). Direct agent CLI invocations load `SshRunner`
  settings from `config/agents.env` (the server addresses, SSH user, key path
  and SSH port). The EU address is used for both SSH and EU service
  configuration. Copy
  `config/agents.env.example` to the ignored `config/agents.env` and fill it
  in locally. The planned
  `prod_env_host` instead loads production-only settings from
  `config/prod.env` and injects `SshRunner` instances into agent functions;
  this prevents production orchestration from depending on test settings or
  direct-agent CLI configuration. Production agents run from a controller
  over SSH.
- Agents receive provider-specific values (such as a VM's private address) as
  parameters; the test orchestrator resolves them in the test environment,
  direct agent CLI invocations load them from `agents.env`, and the planned
  `prod_env_host` supplies them from `prod.env`.

## Firewall Requirements

### Principles

- Implement required firewall rules inside each VM's Ubuntu operating system.
  Do not rely on a cloud-provider firewall as the only enforcement layer.
  Cloud-provider firewall rules may be used as an additional defense, but the
  guest operating system must retain and enforce the required network policy.
- Restrictions are enforced only on `ru-server` and `eu-server`. No firewall
  rules are configured on VPN clients, and no client (including `test-client`)
  has special rules: `test-client` is an ordinary VPN client.
- Where a default-deny policy applies, every allowed flow is listed
  explicitly. Traffic that matches no allow rule is logged and denied.
- IPv6 follows the same rules as IPv4.
- Rule order in the input chain: loopback and established/related replies;
  minimal VM services; RKN crawler denial; project services (including SSH and VPN);
  log and deny the rest. On both servers crawlers are denied before SSH and
  VPN are evaluated, so they cannot reach any service.
- A log rule and its verdict are separate rules. A rate-limited log rule must
  never carry the verdict, otherwise packets over the log rate would skip the
  verdict and reach later allow rules.
- Every denial is logged with the prefix `fck-rkn <host>` (for example,
  `fck-rkn ru`), rate-limited so logs cannot be flooded.
- Rules are applied transactionally with a backup and automatic rollback on
  failure, so a bad ruleset cannot lock out the VM.
- Rules must coexist with chains managed by Docker and must not remove them.
- Provider-specific values (metadata, DNS and DHCP addresses, peer addresses
  and provider service network ranges) are passed to agents as parameters;
  agents never hard-code or name a provider.

### Minimal VM Services (all servers)

Restrictive rules must never break the VM itself. Every default-deny policy
keeps these services working, with the narrowest possible scope.

| Service | Direction | Protocol/port | Peer |
|---------|-----------|---------------|------|
| Loopback | in/out | any | `lo` |
| Established/related replies | in/out | any | connection tracking |
| DNS | out | UDP/TCP 53 | provider DNS resolver |
| DHCP | out/in | UDP 67/68 | provider DHCP server |
| Provider services (`ru-server` only) | out | TCP 80 and 443 | provider service network ranges, including the provider metadata address |
| snapd (`ru-server` only) | out | TCP 443 | Canonical network ranges (AS41231) |
| Crawler blacklist refresh (`ru-server` only) | out | TCP 443 | GitHub API and web ranges from the published metadata endpoint |
| NTP | out | UDP 123 | provider/approved time source |
| ICMP (PMTU, neighbor discovery) | in/out | ICMP/ICMPv6 | local subnet |

Provider services are the services the guest OS depends on: the metadata
server (a private example of such a service), the guest agent, login and the
provider APIs. nftables filters by address, not by name, so they are allowed
by network ranges that the orchestrator or `agents.env` passes to the agent
as a parameter. Agents and remote scripts do not name the provider. In the
test environment the provider is Google and the ranges come from Google's
published list (`goog.json`) plus the metadata address; in production the
provider's own ranges are supplied the same way. snapd (the Ubuntu snap store,
`api.snapcraft.io` and its CDN) is allowed through the Canonical ranges taken
from RIPEstat for AS41231. Neither list may overlap an RKN crawler range, so
no crawler source can match these rules; the lists are re-checked against the
blacklist whenever the policy is applied. The ranges are fetched on every
policy application and validated (non-empty, valid networks, IPv4 and IPv6
accepted); on any failure the previously active rules stay in place, as for
the crawler blacklist. These rules are needed on `ru-server` only, because
`eu-server` has no outgoing restrictions.

SSH is allowed per server as described below. Other package repositories,
container registries, and monitoring endpoints (TCP 443) stay closed on
`ru-server` by default and are opened only for maintenance or from an
approved address list.

The crawler blacklist is fetched and refreshed locally on both servers by a
daily systemd timer. `ru-server` permits only TCP/443 to the published GitHub
API and web ranges needed to fetch the metadata and blacklist; these ranges
are refreshed together with the crawler list. Failure to fetch or validate
any required update data leaves the saved and active firewall rules unchanged.

### ru-server

Incoming (default deny):

- Connections from RKN crawler address ranges are logged and denied before
  the SSH and VPN rules are evaluated, so crawlers cannot reach SSH either.
- SSH is allowed from all IP addresses.
- VPN connections are allowed.
- All other incoming connections are logged and denied.

Outgoing (default deny):

- Minimal VM services are allowed.
- The crawler updater may connect to GitHub API and web ranges on TCP/443.
- All loopback traffic is allowed. It carries the local web-proxy and MTProto
  forwards, which reach `eu-server` only through the VPN, and traffic that is
  addressed to `ru-server` itself.
- The VPN transport to `eu-server` (its Outline address and port) is allowed.
- All other outgoing requests are logged and denied. In particular, direct
  connections to `eu-server` on the web proxy and MTProto ports are denied;
  only the VPN is allowed between the servers.

Forwarded traffic (default deny):

- Only established/related replies are allowed; everything else routed through
  `ru-server` is logged and denied.

VPN clients (all of them, not only `test-client`):

- Traffic inside the VPN addressed to `ru-server` itself is not restricted,
  including ICMP to the client's default route.
- A VPN client cannot send packets beyond `ru-server`; for example, it cannot
  ping external services.
- VPN clients can use only the web proxy and the MTProto proxy; all other
  traffic from them that must leave `ru-server` is logged and denied.
- Outline is a Shadowsocks (application-layer) proxy without layer-3
  forwarding, and its containers use the host network. Requests of VPN
  clients therefore leave `ru-server` through the output hook, not the
  forward hook. The output default-deny policy above is what restricts VPN
  clients to the web proxy and the MTProto proxy: apart from the approved
  crawler-blacklist HTTPS refresh, nothing but loopback forwards, the VPN
  transport to `eu-server` and minimal VM services may leave the server. Do
  not distinguish client traffic from host traffic with per-process (cgroup
  or UID) rules, and do not add rules for `test-client`.

### eu-server

Incoming (default deny):

- SSH is allowed from all IP addresses.
- VPN connections are allowed only from `ru-server`.
- Connections from RKN crawler address ranges are logged and denied before
  the SSH and VPN rules are evaluated, so crawlers cannot reach SSH either.
- All other incoming connections are logged and denied.
- Container-published proxy ports are covered by the same policy through the
  forward hook.

Outgoing:

- There are no restrictions.

### Verification

- Tests confirm that the minimal VM services still work after the policy is
  applied (name resolution, time sync, SSH, DHCP lease renewal) and that the
  intended denied traffic is dropped and logged.
- Test cases cover each rule above: SSH, VPN, RKN crawler denial, proxies for
  VPN clients, denied direct traffic between servers, and denied traffic
  beyond `ru-server` for VPN clients.

## Outline Server Image

- The patched Outline Server image (`fck-rkn-shadowbox:no-public-ip-check`)
  is built once, on the orchestrator host, not separately on each VM.
- If the image already exists on the host, the build is skipped and the
  existing image is used.
- The same image is then loaded onto `eu-server` and afterwards onto
  `ru-server` (`docker save` piped through the runner to `docker load`);
  a VM that already has the image skips the load.
- The host needs Docker for this. Image archives travel through the runner's
  stdin, never through argv or logs.

## Testing

- Unit tests run locally. Tests that verify the environment, firewall and
  network behavior run inside GCP virtual machines; do not rely on the host
  for them.
- Tests that verify network behavior must inspect or exercise the Docker
  network configuration and confirm the intended allowed and denied traffic.
- Do not report a test run as successful unless the Docker command exits
  successfully.

## Local Python Setup

- Create a virtual environment and install dependencies before running Python
  scripts locally:

  ```sh
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m fck_rkn
  ```

  `requirements.txt` installs the optional `test-env` dependency extra for
  local GCP test-environment work. A production-only installation can use
  `.venv/bin/python -m pip install .` without that extra.

## Python Project Structure

- Keep application code in a `src`-layout Python package named `fck_rkn`.
  Do not add new application modules at the repository root.
- Organize modules by responsibility: configuration loading and validation
  belong in `config.py`, cloud-provider operations belong in the relevant
  provider package (for example, `gcp/compute.py`), and environment workflows
  belong in `orchestration/`.
- Keep the package entry point in `fck_rkn/__main__.py`, so the preferred
  invocation is `.venv/bin/python -m fck_rkn`.
- If compatibility with an existing root-level script is needed, keep it as a
  thin launcher that delegates to the package; do not duplicate application
  logic there.
- Keep configuration templates and local runtime configuration in the
  repository-root `config/` directory, subject to the rules above.
- Put automated tests under `tests/`, separating fast unit tests from
  integration tests where appropriate. Integration tests that use GCP must
  target only the isolated `fck-rkn-test` environment.
- Define package metadata and dependencies in `pyproject.toml`. Keep
  `requirements.txt` only as a supported installation input if the project
  continues to use it; avoid maintaining conflicting dependency lists.

The target layout is:

```text
config/
  test.env.example
  agents.env.example
pyproject.toml
requirements.txt
test_env_host.py               # optional compatibility launcher
src/
  fck_rkn/
    __init__.py
    __main__.py
    config.py
    agent_config.py
    agents/
    remote/
    gcp/
      __init__.py
      compute.py
      runner.py
    orchestration/
      __init__.py
      test_environment.py
tests/
  unit/
  integration/
```

## GCP Authorization with `gcloud`

Use Google Cloud CLI Application Default Credentials (ADC) when running
`test_env_host` locally. ADC lets the Google Cloud Python client libraries
authenticate as the developer without storing a service-account key in the
repository.

### SSH through IAP

- Use Identity-Aware Proxy (IAP) TCP forwarding for SSH to test VMs. Do not
  assign them external IP addresses. SSH TCP/22 is permitted from any source
  on the scoped VM network tag; IAP is the approved operator access method.
- Enable both the Compute Engine API and the IAP API in the isolated
  `fck-rkn-test` project:

  ```sh
  gcloud services enable compute.googleapis.com iap.googleapis.com \
    --project=fck-rkn-test
  ```

- Grant the operator the IAP TCP forwarding permission, normally through
  `roles/iap.tunnelResourceAccessor`, and the OS Login permissions needed for
  SSH. Use `roles/compute.osLogin` for standard OS Login access; use
  `roles/compute.osAdminLogin` only when administrator privileges are
  required. Provisioning also needs the minimum Compute Engine permissions
  needed to create/read the dedicated regional Cloud Router and configure
  Cloud NAT, instances, and scoped firewall rules; do not grant project-wide
  Owner access. Cloud NAT and its automatically allocated public IP addresses
  can incur charges.
- On the VM's dedicated network tag, allow ingress TCP/22 from any IPv4
  source. VMs have no external IPs. IAP TCP forwarding remains the approved
  operator connection method, but SSH is not restricted by source CIDR.
- Connect using `gcloud compute ssh` with the `--tunnel-through-iap` option.
  This keeps SSH reachable when the operator's public IP changes and avoids
  exposing the VM directly to the Internet.
- `test_env_host` must provision VMs without external IPs, configure the
  scoped SSH firewall rule, and wait for SSH readiness through IAP.
  Its implementation and configuration must not depend on a fixed public IP
  or `GCP_SSH_SOURCE_CIDR`.
- For setup, execution, expected behavior, and troubleshooting, see
  [`TEST_ENV_HOST.md`](TEST_ENV_HOST.md).

1. Install and initialize the Google Cloud CLI (`gcloud`) using Google's
   official installation instructions. Sign in with the Google account that
   has access to the `fck-rkn-test` project:

   ```sh
   gcloud auth login
   ```

   This signs in the CLI but does not, by itself, create ADC credentials for
   Python client libraries.

2. Create ADC credentials for local application use:

   ```sh
   gcloud auth application-default login
   ```

   Complete the browser authorization flow with the intended Google account.
   The ADC credentials are stored in the user's local gcloud configuration
   directory and are discovered automatically by Google Cloud client
   libraries. Do not copy this credentials file into the repository, a VM
   image, a Docker image, or an unprotected shared location.

3. Set the CLI's default project for convenience and confirm the active CLI
   account:

   ```sh
   gcloud config set project fck-rkn-test
   gcloud auth list
   ```

   Replace the placeholder with the actual project ID. `test_env_host` gets
   the project it checks from the untracked `config/test.env`; changing the
   gcloud CLI default project does not replace or modify that file.

4. Ensure the selected account has permission to read the configured GCP
   project's metadata. Enable the Compute Engine API for the project:

   ```sh
   gcloud services enable compute.googleapis.com \
     --project=YOUR_FCK_RKN_TEST_PROJECT_ID
   ```

   Verify that the API is enabled:

   ```sh
   gcloud services list --enabled \
     --project=YOUR_FCK_RKN_TEST_PROJECT_ID \
     --filter="config.name:compute.googleapis.com"
   ```

   Set the ADC quota project to the same project to avoid quota attribution
   and API enablement errors:

   ```sh
   gcloud auth application-default set-quota-project \
     YOUR_FCK_RKN_TEST_PROJECT_ID
   ```

   Replace both placeholders with the project ID stored in
   `config/test.env`. These commands require the appropriate project
   permissions; enabling an API may require Service Usage permissions, while
   setting the ADC quota project requires permission to use that project for
   quota. API enablement can take several minutes to propagate, so wait and
   retry if the first API request still reports that Compute Engine is
   disabled.

   The preflight performed by `test_env_host` uses ADC to call the Compute
   Engine API and fails before provisioning if authentication or project
   access fails. Provisioning additionally requires permission to create
   Compute Engine instances, use the selected subnet, manage the scoped
   VPC firewall rule, and log in through OS Login. Prefer narrowly scoped
   custom roles or appropriately scoped predefined roles rather than granting
   project-wide Owner access.

5. Create and fill the ignored `config/test.env` automatically. Running
   `.venv/bin/test-env-host` without an existing `config/test.env` creates it
   (the same way as `configure`) before the environment checks, so the file
   never has to be copied by hand; if creation fails, it stops with an
   actionable message and leaves no partial file. An existing file is never
   modified by a normal run. To refresh values later, run
   `.venv/bin/test-env-host configure`. It copies `config/test.env.example`
   when the file is missing, keeps values you already set, adds missing
   variables, and reads `GCP_SUBNET_CIDR` from the configured subnetwork
   through your GCP access. If the configured subnetwork does not exist and
   the network has exactly one subnetwork in the region, it selects that one.
   It also checks that the network, image family and machine type exist, and
   warns when a manually set `GCP_SUBNET_CIDR` differs from the subnetwork.
   It changes nothing if a check or GCP access fails. Confirm
   that the file targets `fck-rkn-test`. If it contains the obsolete `GCP_SSH_SOURCE_CIDR`
   or `GCP_IAP_SOURCE_RANGE` setting from an earlier setup, those entries are
   no longer required. Then run the orchestrator from the repository root:

   ```sh
   .venv/bin/python -m fck_rkn
   ```

   This command can create the test VMs and the IAP SSH firewall rule in
   GCP, which may incur charges. Run it only when provisioning is intended and
   confirm the project and budget first.

- Do not use `GOOGLE_APPLICATION_CREDENTIALS` with a downloaded
  service-account key as a substitute for ADC user login unless a separately
  approved automation environment explicitly requires it.
- Never print, commit, upload, or share ADC credentials or access tokens.
  Revoke local ADC credentials when no longer needed:

  ```sh
  gcloud auth application-default revoke
  ```

- `test_env_host` validates `config/test.env` and uses Application Default
  Credentials to call the Compute Engine API and verify access to the
  isolated `fck-rkn-test` project; it rejects any other project ID. The
  identity needs permission to read project metadata, and the Compute Engine
  API must be enabled.
- If configuration parsing fails, the CLI reports the filename and malformed
  line number without printing that line's contents. Fix the indicated entry
  so it uses `KEY=value` syntax. Do not copy credentials into tracked files or
  share the local config file.
- The CLI writes timestamped INFO logs to the console while validating
  configuration, checking GCP access, applying firewall rules, provisioning
  the VM, and waiting for IAP SSH readiness. Logs describe progress and error
  details without printing local configuration contents or credentials.
- The `eu-server` provisioning defaults in `config/test.env.example` use the
  `europe-north1-a` zone, `e2-micro`, Ubuntu 24.04 LTS, and the `default` VPC
  and subnet. VMs must have no external IP. SSH uses OS Login through IAP;
  firewall rules allow TCP/22 from any IPv4 source, targeting the VM's
  dedicated SSH network tag. Ensure the active `gcloud` account is the
  same operator identity as ADC and has permission to create Compute Engine
  instances, manage VPC firewall rules, use the selected subnet, use IAP TCP
  forwarding, and log in through OS Login before running the orchestrator.
  Typical predefined roles include `roles/compute.instanceAdmin.v1`,
  `roles/compute.networkAdmin`, `roles/compute.networkUser` on the selected
  VPC, `roles/iap.tunnelResourceAccessor`, and `roles/compute.osLogin`; prefer
  custom least-privilege roles where practical. It will reuse an existing VM
  and fail rather than silently change it if the VM is stopped or its SSH
  firewall rules conflict with the approved configuration.
- Do not install project dependencies into the system Python environment.

## Changes

- Keep changes focused on the requested behavior and avoid unrelated edits.
- Update tests and documentation when a change affects behavior or the
  development workflow.
- Never put credentials or other secrets in source control, test fixtures,
  logs, or example configuration. Use placeholders in examples.
