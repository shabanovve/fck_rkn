# Development Guidelines

## Language

- Write all project content in English. This applies to source code, comments,
  docstrings, tests, documentation, configuration examples, commit messages,
  and user-facing text.
- Use clear, descriptive names and consistent formatting.

## Virtual Machines

- Use Ubuntu as the operating system for all project virtual machines.

## Git-tracked File Safety

- Files tracked by Git must not contain concrete IPv4 or IPv6 addresses or
  CIDR ranges, including public service ranges such as the IAP TCP forwarding
  source range. Use descriptive placeholders in tracked templates and store
  required real network values only in ignored local configuration or obtain
  them at runtime from an authoritative source.
- Files tracked by Git must never contain credentials or credential material,
  including passwords, private keys, access tokens, ADC files, or service
  account keys. Use placeholders in examples and keep real credentials in an
  approved local or managed secret store.
- Do not put concrete IP addresses or credentials into source, tests,
  documentation, fixtures, or commit messages. Test with injected settings or
  non-routable symbolic placeholders instead.

## Environments

- Name the test environment `fck-rkn-test`.
- Name the production environment `fck_rkn`.
- Keep test and production resources, credentials, and configuration
  separated.
- Every GCP test VM must have outbound Internet access through Cloud NAT and
  must not have an external IP address. Do not use VM external IPs as an
  outbound-access substitute. Configure Cloud NAT for the VM's subnet and
  region, and verify outbound access through NAT as part of the relevant test
  workflow.
- Re-running Squid deployment must leave an already-running Squid container
  untouched; replace it only when it is not running.
- Re-running Outline Server deployment must leave an already-running,
  correctly configured Outline container untouched. Refuse to replace an
  existing Outline container with unexpected configuration.
- The EU Outline Server image must be rebuilt from the official image with
  `outline-ss-server` patched to accept private target addresses. Use it only
  in the isolated test environment because it removes the server's protection
  against requests to private networks. Production must not use it: production
  servers have real public IP addresses, so private targets are not an issue,
  and production uses the standard Outline Docker image from the public
  registry.
- Provide a manual `test-env-host shutdown-vms` action that validates access
  to the isolated `fck-rkn-test` project, finds running Compute Engine VMs
  across all zones, stops them, and waits for the stop operations to complete.
  It must not delete VMs or their disks, network resources, or firewall rules,
  and must not run as part of the normal provisioning workflow.
- The repository's `config/` directory may contain only the tracked
  `config/test.env.example` template. Local configuration files, generated
  files, credentials, and any other files under `config/` must not be added
  to the repository.
- Keep real environment values out of the example; use placeholders there.

## VPN User Provisioning

- Provide scripts to create VPN users or access keys for each Outline Server.
- `eu-server-agent` and `ru-server-agent` must run the appropriate user
  provisioning script after Outline Server is deployed and running, before
  the corresponding client connectivity tests.
- Keep generated access keys and other credentials in a protected secret
  store; never commit them or write them to unredacted logs.

## Platform Independence and Firewall

- Keep the production deployment independent of any specific cloud provider.
  GCP is the platform for the test environment only.
- Implement required firewall rules inside each VM's Ubuntu operating system.
  Do not rely on a cloud-provider firewall as the only enforcement layer.
- Cloud-provider firewall rules may be used as an additional defense, but the
  guest operating system must retain and enforce the required network policy.

## Testing

- Run automated tests inside GCP virtual machines; do not rely on tests run
  directly on the host.
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
pyproject.toml
requirements.txt
test_env_host.py               # optional compatibility launcher
src/
  fck_rkn/
    __init__.py
    __main__.py
    config.py
    gcp/
      __init__.py
      compute.py
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
  assign them external IP addresses and do not allow SSH from an operator's
  public IP CIDR.
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
- On the VM's dedicated network tag, allow ingress TCP/22 only from Google's
  IAP TCP forwarding range currently published in Google's official
  documentation. Set `GCP_IAP_SOURCE_RANGE` in ignored `config/test.env` to
  that range. Do not add a public-wide or operator-IP allow rule for SSH.
  Keep other ingress restrictions in place.
- Connect using `gcloud compute ssh` with the `--tunnel-through-iap` option.
  This keeps SSH reachable when the operator's public IP changes and avoids
  exposing the VM directly to the Internet.
- `test_env_host` must provision VMs without external IPs, configure the
  scoped IAP-only SSH firewall rule, and wait for SSH readiness through IAP.
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
   Compute Engine instances, use the selected subnet, manage the two scoped
   VPC firewall rules, and log in through OS Login. Prefer narrowly scoped
   custom roles or appropriately scoped predefined roles rather than granting
   project-wide Owner access.

5. Create the ignored `config/test.env` from the example and confirm that it
   targets `fck-rkn-test`. If it contains the obsolete
   `GCP_SSH_SOURCE_CIDR` setting from an earlier setup, remove that line.
   Replace the `GCP_IAP_SOURCE_RANGE` placeholder with the current IPv4 `/20`
   range from Google's official IAP TCP forwarding documentation. Keep the
   literal range only in ignored local configuration, never in a tracked file.
   Then run the orchestrator from the repository root:

   ```sh
   .venv/bin/python -m fck_rkn
   ```

   This command can create the `eu-server` VM and IAP SSH firewall rules in
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
  so it uses `KEY=value` syntax. If it reports `GCP_IAP_SOURCE_RANGE` missing,
  add that setting to the ignored `config/test.env` using the current range
  from Google's official IAP TCP forwarding documentation. Do not copy real
  IP ranges or credentials into tracked files or share the local config file.
- The CLI writes timestamped INFO logs to the console while validating
  configuration, checking GCP access, applying firewall rules, provisioning
  the VM, and waiting for IAP SSH readiness. Logs describe progress and error
  details without printing local configuration contents or credentials.
- The `eu-server` provisioning defaults in `config/test.env.example` use the
  `europe-north1-a` zone, `e2-micro`, Ubuntu 24.04 LTS, and the `default` VPC
  and subnet. VMs must have no external IP. SSH uses OS Login through IAP;
  firewall rules allow TCP/22 only from the locally configured IAP source
  range, targeting the VM's dedicated SSH network tag. Set
  `GCP_IAP_SOURCE_RANGE` in ignored `config/test.env` to the current range
  from Google's official IAP TCP forwarding documentation; do not copy the
  literal range into tracked files. Ensure the active `gcloud` account is the
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
