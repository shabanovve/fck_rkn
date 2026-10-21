# Small-Step Development Tasks

This task list follows `DEVELOPMENT_PLAN.md` and requirements in
`README.md` and `DEVELOPMENT.md`. For each deployable behavior, use this
three-task cycle:

1. Add one small piece of functionality to the relevant deployment agent.
2. Add a test for that behavior to the relevant test agent.
3. Make `test_env_host` deploy the agent change and run that test-agent check.

Do not create standalone tasks just to test that SSH connects or ping works.
SSH and ping success are checked as part of the test-agent cycle for the
behavior that requires them.

## Rules for every task

- Write all project files and tests in English.
- Run automated tests inside Docker on Ubuntu VMs in the isolated
  `fck-rkn-test` environment. Network tests must use Docker networks and
  verify actual allowed and denied traffic.
- Never target production `fck_rkn` from test code or `test_env_host`.
- Give every GCP test VM outbound Internet access through Cloud NAT; do not
  assign external IPs to VMs.
- Install firewall rules for plan item 10 only on `ru-server` and
  `eu-server`, inside their Ubuntu guests. Do not install these rules on
  `test-client`; GCP firewall rules may supplement but not replace guest
  enforcement.
- A deployment is not verified until `test_env_host` has run the matching
  test-agent check and it passed.
- Implement and test plan item 10 through the tasks below. Do not infer
  allow/deny behavior for pairs marked `Not defined`.
- Stop at unresolved decisions listed at the end. Do not silently choose
  provider, security, network, cost, or service behavior.

## Prepare `fck-rkn-test` and create `eu-server`

### 1. [x] Add test environment configuration

- **Change:** Add the tracked template `config/test.env.example` with
  a placeholder for the GCP project ID used by `fck-rkn-test`. The local
  `config/test.env` is runtime configuration and must remain untracked.

### 2. [x] Test test environment configuration

- **Test:** On every `test_env_host` invocation, use `pydantic-settings` to
  validate `config/test.env`, then use Application Default Credentials to
  call Compute Engine API and verify access to the configured project before
  any VM operation.

### 3. Find or create `eu-server`

- **Change:** Make `test_env_host` look up an instance named `eu-server` in
  the configured GCP test project and zone. If it is absent, create an Ubuntu
  VM with that name in the configured test network, then wait until it is
  running and SSH-ready.
- **Approved test settings:** `europe-north1-a`, `e2-micro`, Ubuntu 24.04 LTS,
  the `default` VPC/subnet, OS Login through IAP, no external IP, and outbound
  Internet access through Cloud NAT. Allow SSH ingress only from the IAP TCP
  forwarding source range configured in untracked local settings; scope SSH
  firewall rules to `eu-server` and reject conflicting existing resources
  without changing them. Never include the literal range in tracked files.


## First `eu-server-agent` behavior: successful ping over SSH

### 11. [x] Add ping to `eu-server-agent`

- **Change:** Add one action to `eu-server-agent` that connects to
  `eu-server` over SSH and runs `ping youtube.com`; return success only when
  ping succeeds.

### 13. [x] Deploy and run the ping check

- **Orchestration:** Make `test_env_host` deploy/invoke the updated
  `eu-server-agent` after provisioning and IAP SSH readiness; the workflow
  passes only if the agent's SSH execution and remote ping both succeed.

## Squid on `eu-server`

### 14. [x] Add Squid deployment to `eu-server-agent`

- **Change:** Add one deployment action that starts Squid in Docker on
  `eu-server`, listening on TCP port 3128. If the named Squid container is
  already running, leave it unchanged instead of restarting it.

### 15. [x] Add Squid startup test to `eu-server-test-agent`

- **Test:** Add a check for Squid health and TCP port 3128 on `eu-server`.

### 16. [x] Deploy and test Squid startup

- **Orchestration:** Make `test_env_host` deploy the Squid action and then
  run the new `eu-server-test-agent` check.

### 17. [x] Add Squid HTTP request test

- **Test:** Extend `eu-server-test-agent` to send an HTTP request to
  `youtube.com` through Squid and verify the agreed success result.

### 18. [x] Run the Squid HTTP request test

- **Orchestration:** Make `test_env_host` run the HTTP-through-Squid test
  after Squid deployment and startup verification.

## Outline Server on `eu-server`

### 22. [x] Add EU Outline Server deployment

- **Change:** Add an action to `eu-server-agent` that starts the approved
  Outline Server container on `eu-server`. If its correctly configured
  container is already running, leave it unchanged instead of restarting it.

### 23. [x] Add EU Outline health test

- **Test:** Add an `eu-server-test-agent` check for Outline Server health and
  authorized endpoint availability.

### 24. [x] Deploy and test EU Outline health

- **Orchestration:** Make `test_env_host` deploy Outline and run the new
  health test.

### 25. [x] Add EU VPN-user creation script

- **Change:** Add a command that creates or reuses a named access key for
  the EU Outline Server without persisting or logging the URL. Outline Server
  retains its key record in its own state. A direct operator invocation emits
  the URL to stdout for secure handoff; orchestration passes it only in memory.

### 26. [x] Check Outline container state and VPN port

- **Test:** The `eu-server-test-agent` Outline health check verifies the
  Outline container is `running` with the expected image, the authorized
  management API responds, and the VPN access port is open. Do not run a real
  client in a temporary Docker container.

### 27. [x] Run Outline health check in orchestration

- **Orchestration:** `test_env_host` runs the Outline health check after
  deployment and fails with exit code `11` if it does not pass. Orchestration
  does not create test users.

## Create `ru-server`

### 28. [x] Add `ru-server` lookup to `test_env_host`

- **Change:** Make `test_env_host` look up `ru-server` in the configured test
  project, zone, and network.

### 29. [x] Test `ru-server` lookup

- **Test:** In Docker, verify present and absent instance responses.

### 30. [x] Add `ru-server` creation

- **Change:** When absent, make `test_env_host` create an Ubuntu VM named
  `ru-server` in the test network.

### 31. [x] Test `ru-server` creation

- **Test:** In Docker, verify the configured name, project, zone, Ubuntu
  image, and network are passed to GCP.

### 32. [x] Add `ru-server` readiness wait

- **Change:** Make `test_env_host` wait until `ru-server` is running and
  reachable over SSH.

### 33. [x] Test `ru-server` readiness wait

- **Test:** In Docker, verify ready, provisioning failure, and timeout
  outcomes.

## RU Outline client path to EU Squid

### 34. [x] Add Outline feasibility probe to `ru-server-agent`

- **Change:** Add the smallest action that uses the intended Outline client
  to try reaching the EU Squid endpoint from `ru-server`.

### 35. [x] Add feasibility test to `ru-server-test-agent`

- **Test:** Add a test-agent check for the actual route to EU TCP/3128 using
  the real Outline client/server, not a simulated route.
- **Superseded:** The route check was removed from the code; the forwarding
  test in task 41 verifies the real path.

### 36. [x] Run the Outline feasibility test

- **Orchestration:** The RU probe remains available
  as a diagnostic action, but `test_env_host` no longer runs it. The real
  path is verified by the forwarding test in tasks 41 and 42.
- **Approved:** The client is `ss-tunnel` from the `shadowsocks-libev` Docker
  image, and the forwarding design is a TCP forward through EU Outline to EU
  Squid TCP/3128. Outline/Shadowsocks alone does not provide general layer-3
  routing, so later forwarding tasks must stay within this approved design.

### 37. [x] Add Outline client deployment to `ru-server-agent`

- **Change:** Add an action that deploys the approved Outline client in
  Docker on `ru-server` and connects to EU Outline using a protected test key.

### 38. [x] Add RU Outline client test

- **Test:** Add a `ru-server-test-agent` check that verifies the client is
  connected using the test key.

### 39. [x] Deploy and test the RU Outline client

- **Orchestration:** Make `test_env_host` run the client deployment and then
  the RU client test.

### 40. [x] Add forwarding from RU to EU TCP/3128

- **Change:** Add the approved forwarding action to `ru-server-agent` so
  the EU Squid endpoint is available at the intended RU localhost port.

### 41. [x] Add RU-to-EU forwarding test

- **Test:** Add a `ru-server-test-agent` check that verifies TCP/3128
  forwards to the real EU Squid service and carries an HTTP request.

### 42. [x] Deploy and test RU-to-EU forwarding

- **Orchestration:** Make `test_env_host` deploy forwarding and run the
  forwarding test.

### 42a. [x] Allow private targets in the test Outline Server

- **Change:** `eu-server-agent` builds a derived Outline Server image whose
  `outline-ss-server` accepts private target addresses,
  so the RU forward can reach the private Squid endpoint.

### 42b. [x] Move remote scripts out of Python strings

- **Change:** Shell, Python and Dockerfile snippets live as real files in
  `src/fck_rkn/remote/scripts/` and are loaded with `fck_rkn.remote.render`
  (`@NAME@` placeholders, `#include` fragments, strict unused/missing checks).

### 46. [x] Add tunnel-loss recovery to `ru-server-agent`

- **Change:** Add retry behavior after Outline connection loss while keeping
  client traffic blocked; do not fall back to direct Internet access.
- **Done:** `ru-server-agent recover-tunnel` probes EU Squid through the
  forward and, while it is down, restarts the client and forward containers
  (5 attempts, 10 s apart). It never touches routes or firewall rules and
  fails (exit 1) if the tunnel does not return.

### 47. [x] Add tunnel-loss test to `ru-server-test-agent`

- **Test:** Add a test that verifies traffic is blocked during tunnel loss
  and resumes only after the approved tunnel reconnects.
- **Done:** `ru-server-test-agent tunnel-blocked` passes only while the
  forward container is stopped and no request reaches EU Squid. After the
  tunnel is restored, `squid-forward` verifies resumption. Task 48 wires them.

### 48. [x] Deploy and test tunnel-loss recovery

- **Orchestration:** Make `test_env_host` trigger tunnel loss, run the
  test-agent check, restore the tunnel, and verify recovery.
- **Done:** After the forward check, `test_env_host` runs
  `ru-server-agent stop-tunnel` (exit 20), `tunnel-blocked` (21),
  `recover-tunnel` (22) and a second `squid-forward` check (23).

## Outline Server on `ru-server`

### 49. [x] Add RU Outline Server deployment

- **Change:** Add an action to `ru-server-agent` that starts the approved
  Outline Server container on RU.
- **Done:** `ru-server-agent deploy-outline` reuses `deploy_outline.sh` and the
  same patched image, ports (10000/10001) and state directory as on EU, bound
  to ru-server's private address; it is idempotent. Not yet wired into
  `test_env_host` (task 51).

### 49a. [x] Add a platform-independent `RemoteRunner`

- **Change:** Add `fck_rkn/remote/runner.py` with a `RemoteRunner` protocol
  (`run(command, stdin, timeout, stream_output)` returning an exit code and
  the captured output) and a shared implementation of the timeout, `OSError`
  and non-zero exit handling that is now copied in every agent function.
- **Test:** Unit tests for the shared error handling with a fake runner.
- **Done:** `RemoteRunner`, `RemoteResult`, `RemoteTimeoutError`,
  `RemoteStartError` and `execute()` live in `fck_rkn/remote/runner.py`.
  Agents are migrated in tasks 49d and 49e.

### 49b. [x] Add runner implementations

- **Change:** Add `GcloudIapRunner` (the only place that builds
  `gcloud compute ssh --tunnel-through-iap --project --zone`) and a generic
  `SshRunner` (plain `ssh` with host, user and key path from configuration)
  for production. Both pass secrets through stdin only.
- **Done:** `fck_rkn/gcp/runner.py` (`GcloudIapRunner`) and
  `fck_rkn/remote/ssh.py` (`SshRunner(host, user, key_path, port)`) share
  `fck_rkn/remote/subprocess_runner.py`. Agents are not migrated yet (49d, 49e).
- **Rule:** `GcloudIapRunner` and everything under `fck_rkn/gcp/` may be used
  only by `test_env_host` and test-only code.

### 49c. [x] Split configuration into agent and test-environment parts

- **Change:** Keep the `GCP_*` settings in a test-environment settings class
  used only by `test_env_host` and `fck_rkn/gcp/`. Add platform-neutral agent
  settings (target host and runner selection). Agents must not import
  `fck_rkn.gcp` or `TestEnvironmentSettings`.
- **Done:** `fck_rkn/agent_config.py` (`AgentSettings` from `config/agents.env`:
  `EU_SERVER_HOST`, `RU_SERVER_HOST`, `SSH_USER`, `SSH_KEY_PATH`, `SSH_PORT`)
  with `eu_server_runner` / `ru_server_runner` building `SshRunner`. Production
  agents run remotely over SSH from a controller. `TestEnvironmentSettings`
  stays in `config.py` for `test_env_host` and `fck_rkn/gcp/`; agents still
  import it until 49d and 49e.

### 49d. [x] Make `ru-server-agent` and `ru-server-test-agent` use `RemoteRunner`

- **Change:** Replace `run_remote_with_input` and the `RU_INSTANCE_NAME` /
  `gcloud` usage in `ru_server.py` and `ru_server_test.py` with a runner
  passed in by the caller. `test_env_host` supplies `GcloudIapRunner`.
- **Test:** Existing RU tests use a fake runner; add a check that no module
  under `fck_rkn/agents/` references `gcloud`, `IAP` or `fck_rkn.gcp`.
- **Done:** RU agent functions and `outline.deploy_outline_on` take a
  `RemoteRunner`; their CLIs build an `SshRunner` from `AgentSettings`; the
  orchestrator passes `GcloudIapRunner`. `tests/unit/fake_runner.py` is the
  shared fake; `test_platform_independence.py` checks the migrated modules
  (`eu_server*.py` were added in 49e).

### 49e. [x] Make `eu-server-agent` and `eu-server-test-agent` use `RemoteRunner`

- **Change:** Replace the seven repeated `gcloud` command builders in
  `eu_server.py`, `eu_server_test.py` and `outline.py` with a runner. EU may
  be hosted on GCP in production, but only through the runner configuration.
- **Done:** every function in `eu_server.py` and `eu_server_test.py` takes a
  `RemoteRunner` (a shared `execute` helper replaces the repeated builders;
  `execute` gained `print_stderr` and an optional timeout for the log
  stream). Their CLIs build an `SshRunner` from `AgentSettings`; the
  orchestrator passes a `GcloudIapRunner` for eu-server. `test_platform_independence.py`
  now covers every module under `agents/`.

### 49f. [x] Remove the GCP metadata dependency from remote scripts

- **Change:** `deploy_outline.sh` receives the private IP as a parameter
  instead of querying `metadata.google.internal`. The runner or the
  orchestrator resolves the address (GCP metadata in the test environment, a
  configured address in production).
- **Test:** Rendering test that no script under `remote/scripts/` references
  a cloud provider.
- **Done:** `deploy_outline.sh` takes `@PRIVATE_ADDRESS@` (shell-quoted);
  `deploy_outline_on`, `deploy_outline` and `deploy_outline_server` take
  `private_address`. Production reads `EU_SERVER_PRIVATE_ADDRESS` and
  `RU_SERVER_PRIVATE_ADDRESS` from `config/agents.env`; the orchestrator uses
  `gcp.compute.private_address_of(instance)` (exit code 5 if missing).

### 49g. [x] Document the platform boundary

- **Docs:** Update `DEVELOPMENT.md`, `DEVELOPMENT_PLAN.md` and
  `TEST_ENV_HOST.md`: GCP, IAP and `gcloud` belong only to the test layer;
  agents depend on `RemoteRunner`; production selects `SshRunner` (or another
  runner) by configuration.
- **Open decision:** whether production agents run remotely over SSH from the
  controller (`SshRunner`) or locally on each server; the runner interface
  supports both.
- **Done:** documented in `DEVELOPMENT.md`, `DEVELOPMENT_PLAN.md` and
  `TEST_ENV_HOST.md`. Decision recorded: production agents run from a
  controller over SSH (`SshRunner`).

### 50. [x] Add RU Outline health test

- **Test:** Add a `ru-server-test-agent` check for Outline health and
  authorized client endpoint access.
- **Done:** `ru_server_test.check_outline_health` and CLI action
  `outline-health` run the shared `outline_health_check.sh` (command now
  built once in `agents/outline.py`) on ru-server. Not yet called by the
  orchestrator (task 51).

### 51. [x] Deploy and test RU Outline health

- **Orchestration:** Make `test_env_host` deploy RU Outline and run the new
  health test.
- **Done:** After provisioning `ru-server`, the orchestrator passes its
  private address to `deploy_outline_server`, then runs
  `ru_server_test.check_outline_health`. Failures stop the workflow before
  the Outline access key is created (exit codes 24 and 25).

### 51a. [x] Build the patched Outline image once on the orchestrator host

- **Change:** Build `fck-rkn-shadowbox:no-public-ip-check` locally with
  `docker build` from the same Dockerfile (rendered by `render`). If the image
  already exists on the host (`docker image inspect`), use it and do not
  rebuild. Add a local-build module in the
  test layer; production keeps the per-server build unless decided otherwise.
- **Docs:** Requirement recorded in `DEVELOPMENT.md` ("Outline Server Image")
  and `TEST_ENV_HOST.md` (prerequisites).
- **Test:** Unit tests with a fake command executor: build is run once and
  skipped when the image exists; a build failure returns a non-zero code.
- **Done:** `orchestration/outline_image.build_outline_image()` checks
  `docker image inspect`, reuses an existing image, otherwise runs
  `docker build` with the rendered `outline_shadowbox.Dockerfile` on stdin.
  Not yet wired into the orchestrator (51d); the VM scripts still build
  until 51c.

### 51b. [x] Support binary stdin in runners

- **Change:** `RemoteRunner.run` currently takes text `stdin`. Allow `bytes`
  (or a file object / path) in `RemoteRunner`, `run_transport`, `SshRunner`
  and `GcloudIapRunner` so a gzip'd `docker save` stream can be piped without
  loading it into memory or exposing it in argv.
- **Test:** Runner tests with a binary payload; existing text callers are
  unchanged.
- **Done:** `stdin` may be a `Path`; `run_transport` streams the file as raw
  bytes (a missing file raises `RemoteStartError`). `RemoteRunner`, `execute`,
  `SshRunner` and `GcloudIapRunrner` accept `str | Path | None`.

### 51c. [x] Load the prebuilt image on a VM instead of building

- **Change:** Split `deploy_outline.sh`: `load_outline_image.sh` runs
  `gunzip | sudo docker load` from stdin when the expected image is missing;
  `deploy_outline.sh` stops building and only requires the image. Add
  `load_outline_image(runner, image_archive)` to `agents/outline.py`;
  `deploy_outline_on` stays image-agnostic.
- **Test:** Fake-runner tests: load is skipped when the image exists; the
  script has no `docker build`; archive goes through stdin only.
- **Done:** `load_outline_image.sh` (skips when the image exists, else
  `gunzip | docker load`) and `agents/outline.load_outline_image`.
  `deploy_outline.sh` no longer builds; it fails if the image is missing.
  Until 51d the orchestrator and the `deploy-outline` CLIs do not load the
  image, so a real run of Outline deployment fails at this step.

### 51d. [x] Wire the single build into the orchestrator

- **Orchestration:** Build once before the EU Outline deploy, load the image
  on `eu-server`, then on `ru-server` before its Outline deploy. New exit
  codes for build and load failures.
- **Test:** Orchestrator test: build runs once; load runs for both runners
  before each deployment; failures stop the workflow.
- **Done:** `orchestration/outline_image.export_outline_image` (docker save +
  gzip). The orchestrator builds once after the EU Squid checks, loads the
  archive on `eu-server` before its Outline deploy and on `ru-server` before
  its Outline deploy. Exit codes: 26 build/export, 27 EU load, 28 RU load.
  The agent `deploy-outline` CLIs expect the image to be loaded already.

## Create and test `test-client`

### 55. [x] Add `test-client` lookup

- **Change:** Make `test_env_host` look up `test-client` in the isolated
  `fck-rkn-test` project.
- **Done:** `gcp.compute.lookup_test_client` returns the existing VM or
  `None` without creating it, and rejects a VM outside the configured test
  network (the check is shared with `lookup_ru_server`). The orchestrator
  calls it after the tunnel-loss checks and logs the result; a lookup error
  stops with exit code 29. Its Docker tests are part of this change.

### 56. [x] Test `test-client` lookup

- **Test:** In Docker, verify present and absent VM results.
- **Done:** Covered together with 55 in `tests/unit/test_compute_provisioning.py`
  (present, absent, wrong network) and `tests/unit/test_orchestrator_ping.py`.

### 57. [x] Add `test-client` creation

- **Change:** When absent, make `test_env_host` create an Ubuntu VM named
  `test-client` in the isolated test network.
- **Done:** `gcp.compute.build_test_client` builds the VM like the other test
  servers (Ubuntu family from settings, test subnet, no external IP).
  `ensure_test_client` creates it when absent through the shared
  `_ensure_server`, which also waits for RUNNING and SSH readiness (59). The
  orchestrator logs the lookup result, then calls `ensure_test_client`; any
  error stops with exit code 29.

### 58. [x] Test `test-client` creation

- **Test:** In Docker, verify the expected VM name, project, zone, Ubuntu
  image, and network.
- **Done:** `test_test_client_resource_matches_the_test_environment` and
  `test_test_client_is_created_and_waited_for_until_ssh_ready`.

### 59. [x] Add `test-client` readiness wait

- **Change:** Make `test_env_host` wait until the test client is running and
  SSH-ready.
- **Done:** Covered by 57: `ensure_test_client` waits for RUNNING and SSH.

### 60. [x] Test `test-client` readiness wait

- **Test:** In Docker, verify ready, provisioning failure, and timeout.
- **Done:** Ready, running-wait timeout and SSH-wait timeout are tested for
  `test-client`; a failure from `ensure_test_client` gives exit code 29.

### 61. [x] Add Outline connection to `test-client-agent`

- **Change:** Add an action to connect from `test-client` to RU Outline using
  the protected RU test key.
- **Done:** `test-client-agent connect-outline` (`agents/test_client.py`) reads
  the `ss://` key from stdin and starts a persistent `ss-local` container
  `fck-rkn-test-client-outline` (private config, `--restart unless-stopped`),
  reusing the RU client script. `TEST_CLIENT_HOST` was added to the agent
  settings. The key is created on RU Outline by the orchestrator with
  `create_outline_user` through the RU runner (task 63).

### 62. [x] Add test-client Outline connection test

- **Test:** Add a `test-client-agent` check for the authenticated RU Outline
  connection.
- **Done:** `test-client-agent check-outline` reads the key from stdin
  and runs the shared `outline_client_check.sh` against the
  `fck-rkn-test-client-outline` container: image and running state, config
  equal to the test key, local SOCKS listener, reachability of the Outline
  server, and an HTTP request through the tunnel. The script's server message
  is now neutral (no "EU").

### 63. [x] Connect and test `test-client`

- **Orchestration:** Make `test_env_host` deploy/invoke the connection action
  and run its test-agent check.
- **Done:** After `test-client` is SSH-ready, the orchestrator creates the
  `test-client` access key on RU Outline with `create_outline_user` through
  the RU runner (exit 30), runs `connect_to_outline` (31) and then
  `check_outline_connection` (32) over the IAP runner. The key is passed only
  through stdin.

### 64. [x] Add the approved proxy address to `test-client-agent`

- **Change:** Configure `test-client-agent` to use the confirmed service
  address/listener. Do not assume Outline provides a router or layer-3
  gateway.
- **Done:** Owner decision: the approved listener is a local TCP forward
  on `test-client`, 127.0.0.1:3128, to EU Squid's private address through RU
  Outline. `test-client-agent connect-squid` reads the RU key from stdin and
  starts a persistent `ss-tunnel` container `fck-rkn-test-client-squid`; the
  EU address comes from `EU_SERVER_PRIVATE_ADDRESS`. The orchestrator wiring
  is task 66.

### 65. [x] Add TCP/3128 check to `test-client-agent`

- **Test:** Add a check for TCP/3128 at the approved endpoint.
  - Done: `test-client-agent check-squid` verifies that the tunnel container
    runs and localhost TCP/3128 accepts connections; it needs no key.

### 66. [x] Deploy and test client TCP/3128 access

- **Orchestration:** Make `test_env_host` invoke the client check and verify
  that the path reaches EU Squid through RU.
- **Done:** After the Outline connection, the orchestrator deploys the tunnel
  with the EU private address (exit 33) and runs `check_squid_tunnel` (exit
  34), which also sends an HTTP request through 127.0.0.1:3128 and requires a
  Squid response.

### 67. [x] Add HTTP request to `test-client-agent`

- **Change:** Add an HTTP request to `youtube.com` through the intended
  client-to-RU-to-EU Squid path.
- **Done:** `test-client-agent request-youtube` sends the request through
  127.0.0.1:3128 and prints the HTTP status. It fails only when no HTTP
  response arrives; judging the status is task 68.

### 68. [x] Add HTTP result assertion to `test-client-agent`

- **Test:** Verify the request satisfies the owner-approved HTTP success
  condition and fails when the proxy path is unavailable.
- **Done:** Owner-approved success condition: HTTP status 2xx or 3xx and a
  Squid `Via`/`Server` header. `request-youtube` now asserts it and fails on a
  missing response, another status, or a non-Squid response. Task 67's
  request is the same script.

### 69. [x] Run the full client request test

- **Orchestration:** Make `test_env_host` run `test-client-agent` after
  deployment and require successful proxy handling with no direct fallback.
- **Done:** After the tunnel check the orchestrator runs
  `request_youtube` on `test-client` (exit 35). The request goes only through
  the local proxy 127.0.0.1:3128 with no direct fallback, and the assertion
  requires a Squid response, so a direct answer would fail.

## Other README requirements

### 70. [x] Add MTProxy deployment to `eu-server-agent`

- **Change:** Add one action to deploy the approved MTProxy Docker service
  on EU with its secret supplied securely.
- **Done:** Owner decisions: the official `telegrammessenger/proxy` image and
  a secret generated on EU at the first deployment. `eu-server-agent
  deploy-mtproxy` stores the secret root-only in `/opt/fck-rkn-mtproxy`
  (mounted as the image's `/data`), never prints or logs it, and leaves a
  running container unchanged. Host port 8443 maps to the image's 443. The
  image tag is not pinned yet.

### 71. [x] Add MTProxy test to `eu-server-test-agent`

- **Test:** Add a health/connectivity check for MTProxy and assert its secret
  does not appear in output.
- **Done:** `eu-server-test-agent mtproxy` checks that the container runs, the
  TCP port accepts connections, and the secret file holds 32 hex characters
  with mode 600. It uses `grep -q`/`stat`, so the secret is never printed. The
  tests run the real script against fakes and assert the secret is absent from
  its output. The check does not inspect the container's own logs.

### 72. [x] Deploy and test MTProxy

- **Orchestration:** Make `test_env_host` deploy MTProxy and run the EU
  test-agent check.
- **Done:** After the EU Outline health check the orchestrator runs
  `deploy_mtproxy` (exit 36) and `check_mtproxy` (exit 37) on EU, before RU
  provisioning.

### 73. [x] Add MTProxy forwarding to `ru-server-agent`

- **Change:** Add the approved RU-to-EU forwarding path for MTProxy.
- **Done:** `ru-server-agent deploy-mtproxy-forward` reads the RU Outline key
  from stdin and starts a persistent `ss-tunnel` container
  `fck-rkn-mtproxy-forward` that forwards RU 127.0.0.1:8443 to EU MTProxy
  (port 8443) through Outline, mirroring the Squid forward. It has its own
  container and state directory. Tunnel-loss handling for this forward
  (stop/recovery) is task 75.

### 74. [x] Add RU MTProxy path test

- **Test:** Add a `ru-server-test-agent` check that proves MTProxy traffic
  reaches EU only through the approved Outline path.
- **Done:** `ru-server-test-agent mtproxy-forward` checks that the forward
  container runs, its config goes through the Outline server and ends at the
  MTProxy port, the Outline server is reachable, and localhost TCP/8443
  accepts a handshake-sized write. Limit: it does not complete an MTProto
  handshake, because the secret stays on EU; this must be confirmed on real
  VMs. Denial during tunnel loss is task 75.

### 75. [x] Deploy and test RU MTProxy forwarding

- **Orchestration:** Make `test_env_host` deploy and test the RU MTProxy
  forwarding path, including denial during tunnel loss.
- **Done:** After the Squid forward check, the orchestrator deploys and
  checks the RU MTProxy forward (exits 38, 39). After the Squid tunnel-loss
  steps it stops the MTProxy forward (40), checks that it accepts no
  connections (41), recovers it (42) and re-checks it (43). Not yet verified
  on real VMs.

## Implement plan item 10 traffic restrictions

### 76. Specify inputs for the defined server firewall rules

- **Decision/configuration:** Record the required interfaces, addresses,
  protocols, ports, and connection directions for the defined
  `test-client`-to-`ru-server`, `ru-server`-to-`eu-server`, and
  `any-host`-to-server cases. Identify the authoritative IP source and
  update method for the RKN crawler system that must be blocked from
  `ru-server`. Confirm the IAP SSH source range and required management
  access.
- **Constraint:** Do not define rules for matrix pairs marked `Not defined`.
  Do not apply a blanket default-deny policy that would implicitly decide
  those pairs. This task concerns only plan item 10; the README's daily
  `C24Be/AS_Network_List` blacklist is tracked separately below.

### 77. Add RU server firewall rules

- **Change:** Extend `ru-server-agent` to install and update only the
  plan-item-10 rules in the Ubuntu guest firewall on `ru-server`. Allow
  `test-client` requests only for the approved web-proxy and MTProto paths
  through Outline. Allow `ru-server` requests to `eu-server` only for the
  approved web-proxy and MTProto services through the VPN; deny requests
  from `ru-server` to `test-client` and any other host. Block requests to
  `ru-server` from the configured RKN crawler IPs. Leave other
  `any-host`-to-`ru-server` sources unspecified, except approved IAP SSH
  management. Do not install plan item 10 rules on `test-client`.

### 78. Test RU server firewall rules

- **Test:** Extend `ru-server-test-agent` to verify the allowed
  `test-client` web-proxy/MTProto requests and VPN-carried
  `ru-server`-to-`eu-server` proxy/MTProto requests succeed. Verify
  non-service client requests to RU, other RU-to-EU requests,
  RU-to-`test-client`, RU-to-other-host, and configured crawler-IP requests
  to RU are denied. Do not assert a result for other
  `any-host`-to-`ru-server` sources.

### 79. Deploy and test RU server firewall rules

- **Orchestration:** Make `test_env_host` install RU server firewall rules
  and run the matching test-agent checks after the required proxy paths are
  deployed. Fail if any defined allow/deny check has the wrong result.

### 80. Add EU server firewall rules

- **Change:** Extend `eu-server-agent` to install and update only the
  plan-item-10 rules in the Ubuntu guest firewall on `eu-server`. Allow
  approved web-proxy and MTProto requests from `ru-server` through the VPN.
  Deny direct service requests from `test-client` and service requests from
  any other host; allow SSH management only through the approved IAP path.
  Do not define or enforce rules for EU-originated traffic to
  `test-client`, `ru-server`, or other hosts, since those pairs are
  `Not defined`. Do not install plan item 10 rules on `test-client`.

### 81. Test EU server firewall rules

- **Test:** Extend `eu-server-test-agent` to verify approved
  VPN-carried proxy/MTProto requests from RU succeed, direct client service
  requests and non-IAP service requests from other hosts are denied, and
  approved IAP SSH management works. Do not assert any result for
  EU-originated traffic to pairs marked `Not defined`.

### 82. Deploy and test EU server firewall rules

- **Orchestration:** Make `test_env_host` install EU server firewall rules
  and run the matching test-agent checks after the RU-to-EU paths are
  deployed. Fail if any defined allow/deny check has the wrong result.

### 83. Verify plan item 10 end to end

- **Test/orchestration:** Add isolated network checks for all defined
  allowed and denied paths in the matrix, including stateful responses for
  permitted connections. Run them from `test_env_host` after both server
  firewalls are active. Report each case and fail on any mismatch. Do not
  install firewall rules on `test-client` or assert outcomes for
  `Not defined` pairs.

### 89. Add service and tunnel metrics

- **Change:** Add Prometheus metrics for service health, host resources, and
  tunnel state without credentials or unnecessary per-user details.

### 90. Add metrics tests to test agents

- **Test:** Verify required metrics exist and sentinel secrets are absent.

### 91. Deploy and test Prometheus metrics

- **Orchestration:** Make `test_env_host` deploy metrics collection and run
  the metric checks against test VMs.

### 92. Add Grafana dashboards

- **Change:** Add dashboards for host resources and service health.

### 93. Add dashboard provisioning tests

- **Test:** Verify dashboards and data sources load and required panels exist.

### 94. Deploy and test Grafana

- **Orchestration:** Make `test_env_host` deploy Grafana and run dashboard
  checks in the isolated test environment.

### 98. Add deployment and recovery documentation

- **Change:** Document configuration, deployment, verification, upgrade,
  rollback, cleanup, secret handling, and incident response.

### 99. Test deployment and recovery documentation

- **Test:** Validate required sections and exercise documented commands
  against isolated Ubuntu test VMs from Docker.

### 100. Remove GCP-specific code from production agents

- **Change:** Keep GCP provisioning in `test_env_host`; keep production
  service deployment provider-independent.

### 101. Test production provider independence

- **Test:** Run service-agent tests in Docker without GCP credentials or GCP
  API access.

## Manual shutdown of test VMs

### 102. [x] Add manual shutdown action for all test VMs

- **Requirement:** Provide `.venv/bin/test-env-host shutdown-vms` as an
  explicit operator action. Validate access to the isolated `fck-rkn-test`
  project, enumerate Compute Engine VMs across all zones, stop every VM in
  `RUNNING` state, and wait for each stop operation to complete. Leave VMs in
  other states unchanged. Do not delete VMs, disks, or network resources, and
  do not invoke this action during the normal provisioning workflow.

### 103. [x] Test manual shutdown behavior

- **Test:** Verify shutdown is limited to the configured test project,
  enumerates all zones, stops running VMs only, waits for stop operations,
  and reports failures without deleting resources.

### 104. [x] Document manual VM shutdown

- **Documentation:** Describe the explicit shutdown command, its all-zones
  scope, resources it leaves intact, and that normal provisioning does not
  stop VMs.

## Decisions that block dependent tasks

- GCP project ID, billing/account, region/zone, Ubuntu image, VM size/cost,
  network/subnet, SSH method, and operator access boundary.
- Whether ping is required to succeed and the exact HTTP success condition.
- Outline client version and the technically proven Outline-to-Squid
  forwarding design.
- The actual proxy listener/address meant by "router IP".
- Protected secret store and test VPN-key handoff.
- Exact `C24Be/AS_Network_List` artifact and freshness policy.
- Authoritative source and update method for RKN crawler IPs; whether they
  are the same list as `C24Be/AS_Network_List`.
- Russian destination source, DNS behavior, and shared-address policy.
- MTProxy image/version and secret delivery.
- Monitoring access, retention, alert recipients, and VM cleanup policy.

## Deferred firewall work

The following are separate from plan item 10 and require their own
change/test/orchestration tasks:

- The daily `C24Be/AS_Network_List` retrieval, validation, atomic activation,
  rollback, and reporting workflow.
- Russian destination-list selection, parsing, and enforcement.
- Firewall restrictions for monitoring access and other traffic not covered
  by plan item 10.
