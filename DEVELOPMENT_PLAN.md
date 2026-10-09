# Development Plan

## Goal

Build a repeatable test environment for an EU server, an RU server, and a test
client. The test environment is named `fck-rkn-test` and uses GCP. The
production environment is named `fck_rkn` and may run on any infrastructure
provider. `test_env_host` is the central orchestrator: it creates and prepares
the virtual environment (GCP VMs), invokes deployment agents to configure
services on those VMs, and invokes test agents to verify each milestone.
Services run in Docker on the VMs.

This plan describes a non-production test environment. Do not use production
credentials or expose an unauthenticated proxy to the public Internet.

## Implementation sequence

### Orchestration responsibility

`test_env_host` owns the end-to-end lifecycle of the test environment:

- Create or reuse the required GCP virtual machines and prepare them for use.
- Wait for VM readiness and provide the appropriate agents with access to
  their target VM.
- Run `eu-server-agent` and `ru-server-agent` to deploy or update services on
  their respective servers.
- Run `eu-server-test-agent`, `ru-server-test-agent`, and
  `test-client-agent` to verify the services and network paths at the
  applicable milestones.
- Enforce the dependency order in this plan, stop on failures, and report
  which provisioning, deployment, or test step failed.
- Never treat a deployment-agent exit status as a substitute for running the
  corresponding test agent.

The agents perform their assigned work; `test_env_host` coordinates when they
run and on which VM. Provisioning, deployment, and validation are therefore
all part of the orchestrated workflow.

### 1. Create the GCP test project

- Create or select a GCP project for the `fck-rkn-test` test environment.
- Enable only the APIs needed for VM provisioning and access.
- Configure billing, IAM, and a dedicated test network before creating VMs.
- Record the project ID, region/zone, network, firewall policy, and resource
  budget in local operator configuration; never commit credentials.
- Keep GCP-specific provisioning and API calls in the test-environment
  orchestration layer; production service deployment and network policy must
  not require GCP.

**Acceptance:** the operator can authenticate to the selected project, and
the required APIs, permissions, and network are confirmed without creating
unapproved resources.

### 2. Create `eu-server-agent`

- Implement a Python agent that connects to the existing `eu-server` over SSH.
- Initially, run `ping youtube.com` remotely and return a nonzero exit status
  if SSH or the command fails.
- Capture command output with secrets excluded from logs.

**Acceptance:** from the orchestrator, the agent connects to the intended VM,
runs the command, and reports its exit status and output. A ping result is
only an ICMP connectivity observation; it does not prove HTTP or proxy access.

### 3. Create `test_env_host` and provision `eu-server`

- Implement a Python orchestrator that queries GCP for a VM named
  `eu-server` in the selected project and zone.
- If it does not exist, create the approved lowest-cost suitable VM in the
  test network.
- Wait for the VM to become reachable, then invoke `eu-server-agent` on
  `eu-server`.
- Stop with an error if provisioning, SSH, or the agent command fails.

**Acceptance:** repeated runs reuse the existing VM rather than creating
duplicates; a missing VM is provisioned once and the first SSH/ping milestone
is reported accurately.

### 4. Deploy and test Squid on `eu-server`

- Extend `eu-server-agent` to deploy Squid in a Docker container on
  `eu-server`, listening on TCP port 3128.
- Restrict access to the test network and explicitly approved test-client
  sources. Do not create a public open proxy.
- Configure the required host firewall rules inside the Ubuntu VM. GCP
  firewall rules may provide an additional boundary, but must not be the sole
  enforcement of the service's network policy.
- Create `eu-server-test-agent` to check TCP port 3128 and issue an HTTP
  request to `youtube.com` through Squid, recording the HTTP result.
- `test_env_host` runs `eu-server-agent` to deploy Squid, then runs
  `eu-server-test-agent` to test it. It fails the workflow if either step
  fails.

**Acceptance:** the test agent reaches port 3128 from an authorized test
source, a request through Squid succeeds, and unauthorized sources cannot use
the proxy.

### 5. Deploy and test Outline Server on `eu-server`

- Extend `eu-server-agent` to deploy the approved Outline Server container
  configuration on `eu-server`.
- Provide and invoke a script to create a VPN user/access key after Outline
  Server is deployed and running, before client connectivity testing.
- Extend `eu-server-test-agent` to verify Outline Server health and test a
  client connection using the non-production access key created by the
  provisioning script.
- `test_env_host` runs `eu-server-agent` to deploy Outline, then runs
  `eu-server-test-agent` to test it. It fails the workflow if either step
  fails.

**Acceptance:** the server reports healthy and an authorized test client can
connect. Access keys are delivered through a protected mechanism and are not
written to source control or unredacted logs.

### 6. Provision `ru-server`

- Extend `test_env_host` to create `ru-server` in the same approved test
  project and network, using the approved VM sizing and access policy.
- Reuse the same idempotent provisioning, readiness, and SSH handling used
  for `eu-server`.

**Acceptance:** repeated runs do not create duplicate VMs, and only the
approved management and test traffic can reach the new instance.

### 7. Deploy and test the RU-to-EU proxy path

- Create `ru-server-agent` to deploy an Outline client in Docker on
  `ru-server` and establish the approved connection to `eu-server`.
- Forward the intended path to the EU Squid service on TCP port 3128, without
  direct Internet fallback.
- Create `ru-server-test-agent` to check port 3128 on localhost and issue an
  HTTP request to `youtube.com` through the configured path.
- `test_env_host` runs `ru-server-agent` to deploy the client and establish
  the path, then runs `ru-server-test-agent` to test it. It fails the workflow
  if either step fails.

**Acceptance:** the RU-side test reaches the EU Squid service through the
approved Outline-based path; arbitrary direct egress is denied; loss of the
Outline connection fails closed and recovery restores only the approved path.

**Technical validation gate:** Outline Server uses Shadowsocks and does not
by itself provide a general layer-3 VPN. Before implementing packet forwarding,
prove that the selected Outline client and forwarding design can provide the
required path to the EU private service on isolated test VMs. Do not silently
substitute another tunnel protocol or claim success based only on a simulated
route.

### 8. Deploy and test Outline Server on `ru-server`

- Extend `ru-server-agent` to deploy the approved Outline Server container
  configuration on `ru-server`.
- Provide and invoke a script to create a VPN user/access key after Outline
  Server is deployed and running, before client connectivity testing.
- Extend `ru-server-test-agent` to verify Outline Server health and
  authorized client access using the non-production access key created by
  the provisioning script.
- `test_env_host` runs `ru-server-agent` to deploy Outline, then runs
  `ru-server-test-agent` to test it. It fails the workflow if either step
  fails.

**Acceptance:** the RU Outline Server is healthy and an authorized test client
can connect; unauthorized access is rejected.

### 9. Provision and test `test-client`

- Extend `test_env_host` to provision a `test-client` VM in the isolated test
  network.
- Create `test-client-agent` to connect to `ru-server` using its Outline
  Server and a protected, non-production test key.
- `test_env_host` runs `test-client-agent` on `test-client` after provisioning
  the VM and completing the RU Outline deployment.
- Determine the actual route/interface address used by the approved design;
  check port 3128 at that address and issue an HTTP request to `youtube.com`
  through the intended proxy path.
- Ensure there is no direct-egress fallback and record the result of each
  check.

**Acceptance:** the test client connects through RU Outline, reaches the
approved proxy path, and successfully completes the HTTP check. Direct
Internet access outside the approved path is denied.

## Cross-cutting implementation requirements

- Make `test_env_host` idempotent: detect existing resources, avoid accidental
  duplicates, and report every create/update action.
- Keep production deployment and firewall policy provider-independent. Agents
  configure and enforce required firewall rules inside each VM's Ubuntu guest
  OS; cloud-provider firewall rules are optional defense in depth, not a
  replacement for the guest firewall.
- Run the appropriate VPN user/access-key provisioning script from
  `eu-server-agent` and `ru-server-agent` after their Outline Server is
  running and before client connectivity tests. Protect generated credentials
  and keep them out of source control and unredacted logs.
- Keep project IDs, zones, instance sizing, network settings, SSH settings,
  and service parameters in validated operator configuration. Keep secret
  values in a protected secret source, never in repository files or command
  logs.
- Use least-privilege GCP IAM and SSH access. Limit proxy ingress to the
  isolated test network and authorized test sources.
- Give each agent a clear exit status; orchestration must stop on failures
  rather than continuing as if a step succeeded.
- Run automated tests inside Docker and verify the isolated Docker/GCP test
  network configuration. Tests must not modify production resources or
  firewall rules.
- Provide a cleanup operation for test VMs and related resources, protected
  against accidentally targeting a non-test project.

## Decisions and inputs required

Resolve these before implementing the affected provisioning or network
milestones:

1. What globally unique GCP project ID should be used for the
   `fck-rkn-test` test environment?
2. Which GCP organization/account, billing account, region, and zone should
   host the test environment?
3. What is the acceptable monthly or per-run cost limit, and what does
   “cheapest instance” mean for the required OS, Docker workload, and network
   performance?
4. Should `test_env_host` run on an operator workstation or a dedicated
   runner? Which authentication method should it use (for example,
   Application Default Credentials or a dedicated service account)?
5. Agents must authenticate over SSH using OS Login through IAP TCP
   forwarding. Test VMs must not have external IPs; allow TCP/22 only from
   the IAP TCP forwarding source range configured locally on a VM-specific
   network tag.
6. Which VPC/subnet and remaining ingress/egress rules should be used?
7. Should `ping youtube.com` be a strict pass/fail requirement? ICMP may be
   blocked independently of DNS and HTTP connectivity.
8. Which HTTP status codes and response conditions count as a successful
   Squid request to `youtube.com`?
9. Should Squid and the Outline endpoints be reachable only over private
   addresses, or is any narrowly restricted public ingress required?
10. Which Outline Server/client versions and test-key provisioning method
    should be used?
11. For the requested “router IP,” do you mean the test client's default
    gateway, its Outline local proxy listener, or another address? Outline
    clients commonly expose a local proxy rather than a layer-3 router.
12. What cleanup policy is required: delete all test VMs after each run, keep
    them between runs, or use a configurable expiration period?
