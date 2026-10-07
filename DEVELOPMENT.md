# Development Guidelines

## Language

- Write all project content in English. This applies to source code, comments,
  docstrings, tests, documentation, configuration examples, commit messages,
  and user-facing text.
- Use clear, descriptive names and consistent formatting.

## Virtual Machines

- Use Ubuntu as the operating system for all project virtual machines.

## Environments

- Name the test environment `fck-rkn-test`.
- Name the production environment `fck_rkn`.
- Keep test and production resources, credentials, and configuration
  separated.
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
  .venv/bin/python test_env_host.py
  ```

## GCP Authorization with `gcloud`

Use Google Cloud CLI Application Default Credentials (ADC) when running
`test_env_host` locally. ADC lets the Google Cloud Python client libraries
authenticate as the developer without storing a service-account key in the
repository.

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
   Engine API and fails before VM operations if authentication or project
   access fails. Grant only the permissions required for the current
   development task; VM creation permissions are not needed for a read-only
   preflight.

5. Run the orchestrator from the repository root after creating
   `config/test.env` from the example and setting its real project ID:

   ```sh
   .venv/bin/python test_env_host.py
   ```

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
  configured test project. The identity needs permission to read project
  metadata, and the Compute Engine API must be enabled.
- Do not install project dependencies into the system Python environment.

## Changes

- Keep changes focused on the requested behavior and avoid unrelated edits.
- Update tests and documentation when a change affects behavior or the
  development workflow.
- Never put credentials or other secrets in source control, test fixtures,
  logs, or example configuration. Use placeholders in examples.
