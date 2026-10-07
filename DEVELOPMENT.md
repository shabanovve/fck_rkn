# Development Guidelines

## Language

- Write all project content in English. This applies to source code, comments,
  docstrings, tests, documentation, configuration examples, commit messages,
  and user-facing text.
- Use clear, descriptive names and consistent formatting.

## Virtual Machines

- Use Ubuntu as the operating system for all project virtual machines.

## Environments

- Name the test environment `fck_rkn_test`.
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

## Changes

- Keep changes focused on the requested behavior and avoid unrelated edits.
- Update tests and documentation when a change affects behavior or the
  development workflow.
- Never put credentials or other secrets in source control, test fixtures,
  logs, or example configuration. Use placeholders in examples.
