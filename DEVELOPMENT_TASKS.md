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
- Enforce required firewall rules inside each Ubuntu VM. GCP firewall rules
  may supplement, but must not replace, guest firewall enforcement.
- A deployment is not verified until `test_env_host` has run the matching
  test-agent check and it passed.
- Firewall setup, firewall-policy testing, and firewall-list updates are
  deferred to a later phase and are not implementation tasks in this list.
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

### 36. Run the Outline feasibility test

- **Orchestration:** Make `test_env_host` invoke the RU probe and its
  test-agent check on isolated Ubuntu VMs. Do not proceed with forwarding
  unless the real path works.
- **Blocked by:** An approved Outline client version and explicitly approved
  forwarding design. Outline/Shadowsocks alone does not provide general
  layer-3 routing.

### 37. Add Outline client deployment to `ru-server-agent`

- **Change:** Add an action that deploys the approved Outline client in
  Docker on `ru-server` and connects to EU Outline using a protected test key.

### 38. Add RU Outline client test

- **Test:** Add a `ru-server-test-agent` check that verifies the client is
  connected using the test key.

### 39. Deploy and test the RU Outline client

- **Orchestration:** Make `test_env_host` run the client deployment and then
  the RU client test.
- **Dependency:** Task 36 must pass.

### 40. Add forwarding from RU to EU TCP/3128

- **Change:** Add the approved forwarding action to `ru-server-agent` so
  the EU Squid endpoint is available at the intended RU localhost port.

### 41. Add RU-to-EU forwarding test

- **Test:** Add a `ru-server-test-agent` check that verifies TCP/3128
  forwards to the real EU Squid service and carries an HTTP request.

### 42. Deploy and test RU-to-EU forwarding

- **Orchestration:** Make `test_env_host` deploy forwarding and run the
  forwarding test.
- **Dependency:** Task 36 must pass.

### 46. Add tunnel-loss recovery to `ru-server-agent`

- **Change:** Add retry behavior after Outline connection loss while keeping
  client traffic blocked; do not fall back to direct Internet access.

### 47. Add tunnel-loss test to `ru-server-test-agent`

- **Test:** Add a test that verifies traffic is blocked during tunnel loss
  and resumes only after the approved tunnel reconnects.

### 48. Deploy and test tunnel-loss recovery

- **Orchestration:** Make `test_env_host` trigger tunnel loss, run the
  test-agent check, restore the tunnel, and verify recovery.

## Outline Server on `ru-server`

### 49. Add RU Outline Server deployment

- **Change:** Add an action to `ru-server-agent` that starts the approved
  Outline Server container on RU.

### 50. Add RU Outline health test

- **Test:** Add a `ru-server-test-agent` check for Outline health and
  authorized client endpoint access.

### 51. Deploy and test RU Outline health

- **Orchestration:** Make `test_env_host` deploy RU Outline and run the new
  health test.

### 52. Add RU VPN-user creation script

- **Change:** Add a script that creates a VPN user/access key for RU Outline.

### 53. Add RU VPN-user creation test

- **Test:** Add a `ru-server-test-agent` check for successful user/key
  creation and authorized use.

### 54. Run RU VPN-user creation and test

- **Orchestration:** Make `test_env_host` invoke user creation only after
  RU Outline is healthy, then run the RU test-agent check.

## Create and test `test-client`

### 55. Add `test-client` lookup

- **Change:** Make `test_env_host` look up `test-client` in the isolated
  `fck-rkn-test` project.

### 56. Test `test-client` lookup

- **Test:** In Docker, verify present and absent VM results.

### 57. Add `test-client` creation

- **Change:** When absent, make `test_env_host` create an Ubuntu VM named
  `test-client` in the isolated test network.

### 58. Test `test-client` creation

- **Test:** In Docker, verify the expected VM name, project, zone, Ubuntu
  image, and network.

### 59. Add `test-client` readiness wait

- **Change:** Make `test_env_host` wait until the test client is running and
  SSH-ready.

### 60. Test `test-client` readiness wait

- **Test:** In Docker, verify ready, provisioning failure, and timeout.

### 61. Add Outline connection to `test-client-agent`

- **Change:** Add an action to connect from `test-client` to RU Outline using
  the protected RU test key.

### 62. Add test-client Outline connection test

- **Test:** Add a `test-client-agent` check for the authenticated RU Outline
  connection.

### 63. Connect and test `test-client`

- **Orchestration:** Make `test_env_host` deploy/invoke the connection action
  and run its test-agent check.

### 64. Add the approved proxy address to `test-client-agent`

- **Change:** Configure `test-client-agent` to use the confirmed service
  address/listener. Do not assume Outline provides a router or layer-3
  gateway.

### 65. Add TCP/3128 check to `test-client-agent`

- **Test:** Add a check for TCP/3128 at the approved endpoint.

### 66. Deploy and test client TCP/3128 access

- **Orchestration:** Make `test_env_host` invoke the client check and verify
  that the path reaches EU Squid through RU.

### 67. Add HTTP request to `test-client-agent`

- **Change:** Add an HTTP request to `youtube.com` through the intended
  client-to-RU-to-EU Squid path.

### 68. Add HTTP result assertion to `test-client-agent`

- **Test:** Verify the request satisfies the owner-approved HTTP success
  condition and fails when the proxy path is unavailable.

### 69. Run the full client request test

- **Orchestration:** Make `test_env_host` run `test-client-agent` after
  deployment and require successful proxy handling with no direct fallback.

## Other README requirements

### 70. Add MTProxy deployment to `eu-server-agent`

- **Change:** Add one action to deploy the approved MTProxy Docker service
  on EU with its secret supplied securely.

### 71. Add MTProxy test to `eu-server-test-agent`

- **Test:** Add a health/connectivity check for MTProxy and assert its secret
  does not appear in output.

### 72. Deploy and test MTProxy

- **Orchestration:** Make `test_env_host` deploy MTProxy and run the EU
  test-agent check.

### 73. Add MTProxy forwarding to `ru-server-agent`

- **Change:** Add the approved RU-to-EU forwarding path for MTProxy.

### 74. Add RU MTProxy path test

- **Test:** Add a `ru-server-test-agent` check that proves MTProxy traffic
  reaches EU only through the approved Outline path.

### 75. Deploy and test RU MTProxy forwarding

- **Orchestration:** Make `test_env_host` deploy and test the RU MTProxy
  forwarding path, including denial during tunnel loss.

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
- Exact ingress blacklist artifact and freshness policy.
- Russian destination source, DNS behavior, and shared-address policy.
- MTProxy image/version and secret delivery.
- Monitoring access, retention, alert recipients, and VM cleanup policy.

## Deferred to the firewall phase

The following are intentionally outside this task list and must be planned as
separate small change/test/orchestration cycles in the later firewall phase:

- Guest-firewall setup and service-specific allow/deny rules on Ubuntu VMs.
- Default-deny ingress/egress enforcement and IPv4/IPv6 policy tests.
- The daily `C24Be/AS_Network_List` retrieval, validation, atomic activation,
  rollback, and reporting workflow.
- Russian destination-list selection, parsing, and enforcement.
- Firewall restrictions for proxy, tunnel, and monitoring access.
