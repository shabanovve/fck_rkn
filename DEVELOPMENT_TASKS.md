# Small-Step Development Tasks

This list follows `DEVELOPMENT_PLAN.md` and requirements in `README.md` and
`DEVELOPMENT.md`. Each numbered task must make exactly one small change:
either add one piece of functionality or add/run the automated test for the
previous piece. Do not combine multiple features into one task.

## Rules for every task

- Write project files and tests in English.
- Run automated tests inside Docker on Ubuntu VMs in the isolated
  `fck_rkn_test` environment. Network tests must use Docker networks and
  assert actual allowed and denied paths.
- Never target production `fck_rkn` from test code or `test_env_host`.
- Configure required firewall rules inside each Ubuntu VM. GCP firewall
  rules may supplement, but must not replace, guest firewall enforcement.
- A deployment is not verified until the appropriate test agent has run and
  passed.
- Stop at unresolved decisions listed at the end. Do not silently choose
  provider, security, network, cost, or service behavior.

## Create `eu-server` and verify it

### 1. Add test project configuration

- **Change:** Add configuration for the GCP project ID used by
  `fck_rkn_test`; keep it distinct from the environment's display name.

### 2. Test test-project configuration

- **Test:** In Docker, verify a valid test project ID loads and empty or
  production targets are rejected.

### 3. Add lookup for `eu-server`

- **Change:** Make `test_env_host` look up `eu-server` in the configured test
  project and zone.

### 4. Test `eu-server` lookup

- **Test:** In Docker, verify existing and missing VMs are distinguished and
  a VM from another project or zone is rejected.

### 5. Add creation of `eu-server`

- **Change:** When lookup finds no instance, make `test_env_host` create an
  Ubuntu VM named `eu-server` in the configured test network.

### 6. Test `eu-server` creation

- **Test:** In Docker, verify the GCP create request uses the expected name,
  Ubuntu image, project, zone, network, and test label.

### 7. Add `eu-server` readiness check

- **Change:** Make `test_env_host` wait until GCP reports `eu-server` as
  running and then wait for the approved SSH endpoint to become available.

### 8. Test `eu-server` readiness

- **Test:** In Docker, cover running/SSH-ready, provisioning error, and
  readiness timeout; errors must produce a nonzero result.

### 9. Add idempotent `eu-server` reuse

- **Change:** Make `test_env_host` reuse an existing `eu-server`
  instead of creating a duplicate.

### 10. Test `eu-server` reuse

- **Test:** In Docker, provision twice and verify only one VM is created;
  verify the second run reuses the existing VM.

## Create, run, and test `eu-server-agent`

### 11. Add the `eu-server-agent` script

- **Change:** Create the Python entry point with a minimal CLI for selecting
  the target server and action.

### 12. Test the `eu-server-agent` CLI

- **Test:** In Docker, verify help output, valid arguments, and nonzero exit
  for missing or invalid arguments.

### 13. Add remote SSH command execution

- **Change:** Add the SSH execution path that `test_env_host` uses to run an
  agent command on `eu-server`.

### 14. Test SSH command execution

- **Test:** In the isolated `fck_rkn_test` environment, run the
  `eu-server-agent` command on the created `eu-server` over SSH and verify
  that stdout, stderr, and the exit status are returned to the orchestrator.

### 15. Add ping action to `eu-server-agent`

- **Change:** Make `eu-server-agent` run `ping youtube.com` on `eu-server` and
  return success only when ping succeeds.

### 21. Run `eu-server-agent` over SSH

- **Change:** Make `test_env_host` invoke `eu-server-agent` on the ready
  `eu-server` over SSH.

## Add Squid and test it on `eu-server`

### 23. Add Squid container deployment

- **Change:** Extend `eu-server-agent` to start Squid in Docker on
  `eu-server`, listening on TCP port 3128.

### 24. Test Squid container startup

- **Test:** In Docker, verify the Squid container starts, becomes healthy,
  and listens on TCP/3128.

### 25. Add the Squid guest-firewall rule

- **Change:** Configure the Ubuntu guest firewall to permit TCP/3128 only
  from approved test sources.

### 26. Test the Squid guest-firewall rule

- **Test:** In Docker networks, verify an approved source can connect and an
  unapproved source cannot; do not rely solely on GCP firewall behavior.

### 27. Add TCP/3128 probe to `eu-server-test-agent`

- **Change:** Make `eu-server-test-agent` check TCP port 3128 and report the
  result independently from an HTTP proxy request.

### 28. Test the TCP/3128 probe

- **Test:** In Docker, verify open port, closed port, timeout, and denied
  source behavior.

### 29. Add HTTP-through-Squid probe

- **Change:** Make `eu-server-test-agent` request `youtube.com` through
  Squid and report the HTTP result.

### 30. Test HTTP-through-Squid behavior

- **Test:** In Docker, verify successful proxy handling and failure on
  connection errors or HTTP results outside the owner-approved condition.

### 31. Run Squid deployment from `test_env_host`

- **Change:** Make `test_env_host` invoke the Squid deployment action in
  `eu-server-agent`.

### 32. Run the Squid test agent from `test_env_host`

- **Test:** Verify in Docker that `test_env_host` runs
  `eu-server-test-agent` only after deployment and fails if either step fails.

## Add Outline and a VPN user on `eu-server`

### 33. Add EU Outline Server deployment

- **Change:** Extend `eu-server-agent` to deploy the approved Outline Server
  container on `eu-server`.

### 34. Test EU Outline Server startup

- **Test:** In Docker, verify container health and endpoint reachability only
  from the authorized test network.

### 35. Add EU VPN-user creation script

- **Change:** Create a script that adds a VPN user/access key to the EU
  Outline Server.

### 36. Test EU VPN-user creation

- **Test:** In Docker, verify key creation and rejection when the server is
  not ready.

### 37. Invoke EU VPN-user script from `eu-server-agent`

- **Change:** Make `eu-server-agent` invoke the script after the EU Outline
  Server has passed its health check.

### 38. Test EU deployment/user-creation order

- **Test:** In Docker, verify server deployment and health precede key
  creation and that earlier failures prevent key creation.

### 39. Add protected EU access-key handoff

- **Change:** Pass the generated non-production key from `eu-server-agent`
  to the EU test agent through the approved protected mechanism.

### 40. Test EU access-key secrecy

- **Test:** In Docker, assert the test agent receives the key and command
  output, logs, and errors do not contain it.

### 41. Add EU Outline connection check

- **Change:** Extend `eu-server-test-agent` to connect with the generated EU
  test key.

### 42. Test EU Outline client access

- **Test:** In Docker with the approved Outline client/server, verify the
  valid key connects and an invalid or revoked key is rejected.

### 43. Run the EU Outline workflow

- **Change:** Make `test_env_host` run EU deployment, user creation, and the
  EU Outline test agent in that order.

### 44. Test the EU Outline workflow

- **Test:** In Docker, verify exact invocation order and failure propagation
  for each step.

## Create `ru-server` and test Outline feasibility

### 45. Add lookup for `ru-server`

- **Change:** Make `test_env_host` look up `ru-server` in the same isolated
  test project, zone, and network.

### 46. Test `ru-server` lookup

- **Test:** In Docker, verify a correct test VM is found and a VM from
  another environment is rejected.

### 47. Add creation of `ru-server`

- **Change:** When absent, make `test_env_host` create an Ubuntu VM named
  `ru-server` in the approved test network.

### 48. Test `ru-server` creation

- **Test:** In Docker, verify the create request's project, Ubuntu image,
  zone, network, name, and test label.

### 49. Add `ru-server` readiness check

- **Change:** Make `test_env_host` wait for `ru-server` to be running and
  SSH-ready.

### 50. Test `ru-server` readiness

- **Test:** In Docker, verify ready, create failure, and timeout behavior.

### 51. Add Outline-to-Squid feasibility test

- **Change:** Add the smallest test harness using the intended Outline
  client/server to check reachability from the RU side to EU Squid.

### 52. Run the Outline-to-Squid feasibility test

- **Test:** In Docker on isolated Ubuntu VMs, exercise the real Outline
  components and verify the actual path to EU TCP/3128; a simulated route
  does not pass.
- **Blocked by:** Approved Outline versions and a concrete forwarding design.
  Outline/Shadowsocks does not inherently provide layer-3 routing.

## Add the RU-to-EU Squid path and test it

### 53. Add Outline client deployment to `ru-server-agent`

- **Change:** Make `ru-server-agent` run the approved Outline client in
  Docker on `ru-server`, using a protected EU test key.

### 54. Test RU Outline client deployment

- **Test:** In Docker, verify client startup, authenticated connection, and
  credential secrecy.
- **Dependency:** Task 52.

### 55. Add forwarding to EU TCP/3128

- **Change:** Configure the approved forwarding mechanism to expose the EU
  Squid service through the intended RU localhost endpoint.

### 56. Test forwarding to EU TCP/3128

- **Test:** In Docker networks, inspect routes/listeners and verify an HTTP
  request reaches the real EU Squid container.
- **Dependency:** Task 52.

### 57. Add RU guest-firewall egress restriction

- **Change:** Configure the Ubuntu guest firewall to permit only the
  approved tunnel and proxy flows and deny direct fallback.

### 58. Test RU guest-firewall egress restriction

- **Test:** In Docker networks, verify the approved service route works and
  arbitrary IPv4/IPv6 egress is denied.

### 59. Add tunnel-loss retry and fail-closed behavior

- **Change:** Make `ru-server-agent` retry the Outline connection while
  keeping client egress blocked after tunnel loss.

### 60. Test tunnel loss and recovery

- **Test:** In Docker, drop and restore the tunnel; verify no direct fallback
  during loss and successful recovery over the approved route.

### 61. Add localhost port check to `ru-server-test-agent`

- **Change:** Create `ru-server-test-agent` functionality to check TCP/3128
  on `ru-server` localhost.

### 62. Test the RU localhost port check

- **Test:** In Docker, verify open, closed, and timed-out port cases.

### 63. Add HTTP-through-RU-path check

- **Change:** Make `ru-server-test-agent` request `youtube.com` through the
  local endpoint forwarded to EU Squid.

### 64. Test the HTTP-through-RU-path check

- **Test:** In Docker networks, verify successful proxy traffic and failure
  when the listener, tunnel, or EU proxy is unavailable.

### 65. Run RU-to-EU deployment from `test_env_host`

- **Change:** Make `test_env_host` run `ru-server-agent` to establish the
  approved Outline client and Squid forwarding path.

### 66. Run `ru-server-test-agent`

- **Test:** Verify in Docker that `test_env_host` runs the test agent after
  deployment and fails on any failed network check.

## Add Outline and a VPN user on `ru-server`

### 67. Add RU Outline Server deployment

- **Change:** Extend `ru-server-agent` to deploy the approved Outline Server
  container on RU.

### 68. Test RU Outline Server startup

- **Test:** In Docker, verify health and access only from authorized test
  sources.

### 69. Add RU VPN-user creation script

- **Change:** Create a script that adds a VPN user/access key to the RU
  Outline Server.

### 70. Test RU VPN-user creation

- **Test:** In Docker, verify successful key creation and failure when the
  RU Outline Server is not ready.

### 71. Invoke RU VPN-user script from `ru-server-agent`

- **Change:** Make `ru-server-agent` invoke the script after RU Outline
  Server passes its health check.

### 72. Test RU deployment/user-creation order

- **Test:** In Docker, verify Outline health precedes user creation and the
  generated key is not logged.

### 73. Add RU Outline connection check

- **Change:** Extend `ru-server-test-agent` to connect to RU Outline with the
  generated test key.

### 74. Test RU Outline client access

- **Test:** In Docker with the approved Outline client/server, verify
  authorized connection and invalid/revoked key rejection.

### 75. Run the RU Outline workflow

- **Change:** Make `test_env_host` run RU deployment, user creation, then
  `ru-server-test-agent`.

### 76. Test the RU Outline workflow

- **Test:** In Docker, verify invocation order and fail-fast behavior.

## Create `test-client` and verify the full path

### 77. Add lookup for `test-client`

- **Change:** Make `test_env_host` look up `test-client` in the isolated
  `fck_rkn_test` project and network.

### 78. Test `test-client` lookup

- **Test:** In Docker, verify the test VM is found and production targets
  are rejected.

### 79. Add creation of `test-client`

- **Change:** When absent, make `test_env_host` create an Ubuntu VM named
  `test-client` with the approved test settings.

### 80. Test `test-client` creation

- **Test:** In Docker, verify create parameters, test labels, and rejection
  of a production target.

### 81. Add `test-client` readiness check

- **Change:** Make `test_env_host` wait until `test-client` is running and
  SSH-ready.

### 82. Test `test-client` readiness

- **Test:** In Docker, verify success, provisioning failure, and timeout.

### 83. Add Outline connection to `test-client-agent`

- **Change:** Make `test-client-agent` connect to RU Outline using the
  protected RU test key.

### 84. Test the client Outline connection

- **Test:** In Docker, verify valid key, invalid key, unavailable RU server,
  and secret redaction.

### 85. Add the confirmed proxy address to client configuration

- **Change:** Configure `test-client-agent` to use the explicitly approved
  endpoint; do not assume Outline creates a router or layer-3 gateway.

### 86. Test the proxy address and route

- **Test:** In Docker, inspect interfaces, routes, listeners, and network
  namespaces to prove the configured endpoint is the intended path.
- **Blocked by:** Outline feasibility and the owner's definition of "router
  IP".

### 87. Add TCP/3128 check to `test-client-agent`

- **Change:** Make the client test agent check TCP/3128 at the confirmed
  endpoint.

### 88. Test the client TCP/3128 check

- **Test:** In Docker, verify permitted connection and closed-port,
  wrong-address, and unauthorized-path failures.

### 89. Add HTTP request to `test-client-agent`

- **Change:** Make the client test agent request `youtube.com` through the
  client-to-RU-to-EU proxy path.

### 90. Test the complete client HTTP path

- **Test:** In Docker on isolated VMs, verify the agreed HTTP success
  condition and deny direct Internet fallback and unrelated egress.

### 91. Run the full test-client workflow

- **Change:** Make `test_env_host` provision the client and invoke
  `test-client-agent` after RU Outline is ready.

### 92. Test full workflow ordering and failure handling

- **Test:** In Docker, verify provisioning, connection, port check, and HTTP
  check order; any failed stage must make the workflow fail.

## Add remaining project requirements

### 93. Correct README environment and security statements

- **Change:** Align `README.md` with environment names, GCP test-only use,
  provider-independent production, Ubuntu, guest firewall enforcement, and
  services actually planned; remove unsupported detection-resistance claims.

### 94. Test README requirements

- **Test:** In Docker, check required statements and reject contradictions
  or unsupported claims.

### 95. Add EU MTProxy deployment

- **Change:** Add the approved MTProxy container to EU deployment with
  protected secret configuration.

### 96. Test EU MTProxy deployment

- **Test:** In Docker, verify health, authorized network access, denied
  unauthorized access, and absence of secrets in logs.

### 97. Add MTProxy path check

- **Change:** Add a test-agent action that checks Telegram MTProxy
  connectivity through the approved EU/RU path.

### 98. Test the MTProxy path check

- **Test:** In Docker networks, verify allowed MTProxy traffic, unauthorized
  access denial, and fail-closed behavior when the tunnel is lost.

### 99. Add guest-firewall default-deny policy

- **Change:** Add approved ingress/egress default-deny rules inside both
  Ubuntu server VMs.

### 100. Test guest-firewall default-deny policy

- **Test:** In Docker networks, verify allowed and denied IPv4/IPv6 service,
  management, DNS, and tunnel traffic.
- **Blocked by:** Approved ports, address sets, DNS behavior, and operational
  exceptions.

### 101. Add ingress blacklist parser

- **Change:** Add parsing for the owner-approved IPv4/IPv6 artifact from
  `C24Be/AS_Network_List`.

### 102. Test ingress blacklist parsing

- **Test:** In Docker, test valid input and malformed, stale, wrong-family,
  and oversized fixture data.
- **Blocked by:** Selection of the exact upstream artifact and freshness
  policy.

### 103. Add daily blacklist update

- **Change:** Add scheduled retrieval, validation, staging, and atomic guest
  firewall activation while preserving the last known-good ruleset.

### 104. Test daily blacklist update and rollback

- **Test:** In Docker, simulate successful update, fetch failure, invalid
  data, activation failure, rollback, and failure reporting without changing
  real firewall rules.

### 105. Add Russian destination-list parser

- **Change:** Add parsing for the separately approved Russian domain/IP
  destination list.

### 106. Test Russian destination-list parsing

- **Test:** In Docker, test listed/unlisted domains, IPv4/IPv6, shared
  addresses, and malformed fixtures.
- **Blocked by:** Approved source, DNS handling, and shared-address policy.

### 107. Add Russian destination enforcement

- **Change:** Add the approved destination policy to the designated guest
  firewall enforcement point.

### 108. Test Russian destination enforcement

- **Test:** In Docker networks, verify listed destinations are blocked,
  unlisted permitted destinations work, and shared addresses follow the
  approved policy.
- **Dependency:** Task 105.

### 109. Add Prometheus service metrics

- **Change:** Expose health metrics for Outline, Squid, MTProxy, and tunnel
  state without secrets or unnecessary per-user data.

### 110. Test Prometheus metrics

- **Test:** In Docker, verify expected metrics and assert no sentinel secret
  appears in metric output.

### 111. Add Grafana dashboards

- **Change:** Add dashboards for service health and host resources.

### 112. Test Grafana dashboard provisioning

- **Test:** In Docker, load dashboards and verify required data sources and
  panels.

### 113. Add monitoring access restriction

- **Change:** Restrict Prometheus and Grafana to the approved operator access
  boundary.

### 114. Test monitoring access restriction

- **Test:** In Docker networks, verify operator access and reject
  unauthorized or public access.

### 115. Add deployment and recovery documentation

- **Change:** Document configuration, deployment, verification, upgrade,
  rollback, cleanup, secret handling, and incident response.

### 116. Test deployment documentation

- **Test:** In Docker, check required runbook sections and exercise documented
  commands against isolated Ubuntu test VMs.

### 117. Remove GCP dependency from production service deployment

- **Change:** Keep GCP API calls in `test_env_host`; make production agents
  and guest firewall deployment provider-independent.

### 118. Test production provider independence

- **Test:** In Docker, run service deployment and firewall tests without GCP
  credentials or API access.

## Decisions that block dependent tasks

- GCP project ID, account/billing, region/zone, Ubuntu image, VM size/cost,
  network/subnet, SSH method, and operator access boundary.
- Whether ping is mandatory and which HTTP response counts as success.
- Outline versions and a technically proven Outline-to-Squid forwarding
  design.
- The actual proxy endpoint meant by "router IP".
- Protected secret store and test access-key handoff mechanism.
- Exact ingress blacklist artifact and freshness policy.
- Russian destination source, DNS behavior, and shared-address policy.
- Firewall ports, address sets, DNS allowances, and operational exceptions.
- MTProxy image/version and secret configuration.
- Monitoring access, retention, alerting, and VM cleanup policy.
