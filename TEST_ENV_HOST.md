# Using `test_env_host`

`test_env_host` prepares the isolated GCP test VM `eu-server`. It validates
local settings, verifies Google Cloud access, creates or checks the scoped SSH
firewall rules, finds or creates the VM, waits for it to run, and checks SSH
readiness through IAP and OS Login.

The orchestrator currently provisions and checks VM access; it does not yet
deploy the service agents or run their tests. Creating the VM and firewall
rules changes GCP resources and can incur charges. Run it only in the
`fck-rkn-test` project when provisioning is intended.

The initial `eu-server-agent` action is available as
`.venv/bin/eu-server-agent`. It runs one bounded `ping youtube.com` from
`eu-server` over IAP SSH. It requires the VM to exist and be SSH-ready; task 13
will wire this action into the orchestrator.

## Prerequisites

- A configured GCP project named `fck-rkn-test`, with billing enabled.
- The Compute Engine and IAP APIs enabled in that project.
- Google Cloud CLI (`gcloud`) installed.
- The operator signed in to the CLI, and Application Default Credentials
  configured for the Python client libraries.
- Permissions to inspect the project, create Compute Engine instances, manage
  the scoped VPC firewall rules, use the selected subnet, establish IAP TCP
  forwarding, and log in through OS Login.
- The IAP source range configured locally, as described below.

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

Open `config/test.env` locally and set `GCP_IAP_SOURCE_RANGE` to the current
IAP TCP forwarding source range from Google's
[official IAP TCP forwarding documentation](https://cloud.google.com/iap/docs/using-tcp-forwarding).
Do not put a concrete IP address, CIDR range, or credential in a Git-tracked
file. Keep `GCP_PROJECT_ID` set to `fck-rkn-test`; the program rejects other
projects.

The remaining template defaults specify the VM zone, machine type, Ubuntu
image family, VPC/subnet, and SSH network tag/firewall rule names. Local
configuration is ignored by Git and must not be committed or shared.

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
failures, and command timeouts return a nonzero status.

## Behavior and safety

- If `eu-server` is absent, the tool creates it without an external IP.
- If it already exists, the tool reuses it only when its configuration meets
  the expected private-network, OS Login, and SSH-tag settings. It does not
  silently modify a mismatched VM.
- The tool creates or verifies VM-scoped SSH firewall rules for IAP access.
  Conflicting existing rules cause provisioning to stop rather than being
  overwritten.
- The tool waits for the VM to enter `RUNNING`, then runs a short
  `gcloud compute ssh --tunnel-through-iap` command to verify OS Login SSH.
- A VM without an external IP does not automatically have general outbound
  internet access. Later tasks that need package downloads or service egress
  may require a separately approved Cloud NAT configuration.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Configuration syntax error | The CLI prints the filename and line number, not the contents. Fix that line to use `KEY=value` syntax. |
| `GCP_IAP_SOURCE_RANGE` missing or invalid | Add the current documented IAP TCP forwarding range to the ignored `config/test.env`. |
| ADC authentication or project access failure | Repeat `gcloud auth application-default login`, confirm the active account and project, and verify the APIs and IAM permissions. |
| No active `gcloud` account | Run `gcloud auth login` and confirm the intended account is active. |
| Firewall rule conflicts | Review the named rule in GCP. The tool will not overwrite a rule whose settings differ from the required configuration. |
| VM configuration mismatch | Review the existing `eu-server`; the tool refuses to silently alter it. |
| VM does not reach `RUNNING` | Check Compute Engine operation errors, quota, billing, selected zone, image, machine type, and subnet. |
| IAP SSH readiness times out | Confirm IAP TCP forwarding permission, OS Login access, enabled IAP API, the configured IAP source range, and the VM's scoped firewall rules. |

The process returns status `0` on success, `2` for invalid or unreadable
configuration, `3` for authentication failures, `4` for project-access
failures, and `5` for provisioning or readiness failures.
