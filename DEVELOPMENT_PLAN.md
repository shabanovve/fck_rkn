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
- Give every GCP test VM outbound Internet access through Cloud NAT without
  assigning external IP addresses.
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
- Configure Cloud NAT for the test VM subnet and region so VMs can access the
  Internet without external IP addresses.
- Record the project ID, region/zone, network, firewall policy, and resource
  budget in local operator configuration; never commit credentials.
- Keep GCP-specific provisioning and API calls in the test-environment
  orchestration layer; production service deployment and network policy must
  not require GCP. Agents depend only on the `RemoteRunner` interface; the
  test environment uses a GCP IAP runner, and production uses an SSH runner
  configured in `config/agents.env` (see `DEVELOPMENT.md`).

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
  `eu-server`, listening on TCP port 3128. If its named container is already
  running, leave it unchanged instead of restarting it.
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
  configuration on `eu-server`. Reuse an already-running container when its
  image and running state match the approved configuration; do not restart it.
- The EU Outline Server must be rebuilt from the official image with
  `outline-ss-server` patched so it accepts private target addresses (the stock
  server rejects them). This lets RU clients reach the private EU Squid
  endpoint through Outline. It removes the server's protection against
  requests to private networks; in production RU requests also reach the EU
  Squid through the tunnel at its private address on port 3128, and this risk
  is accepted.
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

### 10. Define and enforce traffic restrictions

- Apply the following directional policy to service traffic. For pairs with
  a defined policy, treat traffic not explicitly allowed as denied; allow
  stateful response packets only for an explicitly approved connection.
  Pairs marked `Not defined` have no specified rule.

| Request source \ Request handler | `test-client` | `ru-server` | `eu-server` | Any other host |
| --- | --- | --- | --- | --- |
| `test-client` | Local traffic only | Allow only the web-proxy and MTProto service paths through the approved RU Outline connection, including the required connection to the RU Outline endpoint | Deny direct service traffic; reach EU services only through the approved path via `ru-server` | Not defined |
| `ru-server` | Deny requests under the general rule; responses for permitted client-initiated flows only | Local traffic only | Allow only the approved web-proxy and MTProto requests through the VPN tunnel; no other outbound requests | Deny |
| `eu-server` | Not defined | Not defined | Local traffic only | Not defined |
| Any other host | Not defined | Deny requests only from IP addresses of the RKN crawler system; policy for other sources is not defined | Deny, except explicitly approved management access | Not defined |

**Current rollout stage: log-only observation.** On `ru-server`, traffic that
the planned guest firewall would reject is logged with an `fck-rkn ru`
prefix and then allowed to continue. The output chain has an `accept` policy
instead of a default drop; otherwise-unmatched outbound traffic is written
with the `fck-rkn ru output default-policy:` prefix. Log rules are rate-limited
to protect the kernel log. Review kernel logs before changing any individual
log rule into an enforcing drop; do not treat the matrix below as currently
enforced on this VM. GCP VPC firewall behavior is separate and is not changed
by the guest nftables logging rules.

`Not defined` means this plan does not specify a firewall rule for that
source/destination pair; it is not an instruction to allow or deny traffic.
Resolve these pairs before implementing a policy that depends on them.

- Keep management access separate from client/service traffic. The SSH
  management port (TCP/22) must be reachable on every VM from any source.
  Firewall rules must not block SSH based on source address. IAP remains an
  approved connection method, but it is not the only permitted SSH source.
  Do not treat SSH permission as permission for other traffic between servers
  and other hosts.
- Install and enforce these firewall rules only on `ru-server` and
  `eu-server`; do not install firewall rules for this policy on
  `test-client`. Add network tests that verify each allowed path and
  representative denied paths, including direct `test-client`-to-`eu-server`
  access and that requests from `ru-server` are restricted to the approved
  proxy services. Do not infer rules for pairs marked `Not defined`.
- Use nftables for guest firewall enforcement. The web proxy and MTProxy
  services on `eu-server` use TCP ports 3128 and 8443, respectively; access
  from `ru-server` must use the approved Outline forwarding path. Obtain the
  Outline listener address and port from the protected access-key/runtime
  configuration. Resolve server addresses and network interfaces from
  provisioning/runtime configuration; do not hard-code VM addresses or
  interface names.
- Block requests to `ru-server` from the RKN crawler IP ranges using the
  general mixed IPv4/IPv6 blacklist in
  `C24Be/AS_Network_List/blacklists_nftables/blacklist.nft`, fetched from
  that repository's `main` branch, except SSH TCP/22 which is allowed from
  any source. This is the confirmed source for the RKN crawler IP list. The
  upstream project regenerates blacklist artifacts daily.
- Update firewall configuration transactionally: stage and validate the
  complete replacement rules and blacklist before activation. Require
  non-empty blacklist data with both IPv4 and IPv6 sets and at least one
  network, and validate nftables syntax before activation. Treat unavailable,
  empty, invalid, or un-installable replacement data as an update failure.
  On failure, retain the previous saved configuration, report the error,
  and keep the firewall running with its previous active rules; never leave
  a partially applied policy. If there is no previous valid configuration,
  fail without activating a partial one.

**Final enforcement acceptance:** after the log-only observation period and
review, only the flows listed above should succeed; direct client access to
EU services must be blocked, and `ru-server` must send requests only to the
approved proxy services. SSH TCP/22 remains reachable on every VM from any source.
Policy firewall rules are installed only on the two servers, not on
`test-client`. Failed updates preserve the previous saved and active rules and
report an error. Unspecified pairs remain unresolved until explicitly
decided.

## Cross-cutting implementation requirements

- Make `test_env_host` idempotent: detect existing resources, avoid accidental
  duplicates, and report every create/update action.
- Keep production deployment and firewall policy provider-independent. Agents
  configure and enforce required firewall rules inside the Ubuntu guest OS
  of each server; cloud-provider firewall rules are optional defense in
  depth, not a replacement for the guest firewall. Do not install these
  policy rules on `test-client`.
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
5. **Resolved:** Agents use OS Login through IAP TCP forwarding for SSH.
   Test VMs must not have external IPs, and their outbound Internet access
   must use Cloud NAT. Allow SSH TCP/22 from any source on a VM-specific
   network tag; guest firewalls must also permit SSH from any source.
6. Which VPC/subnet and remaining ingress/egress rules should be used?
7. Should `ping youtube.com` be a strict pass/fail requirement? ICMP may be
   blocked independently of DNS and HTTP connectivity.
8. *Resolved:* success is an HTTP status 2xx or 3xx and a Squid
   `Via`/`Server` header. Original question: which HTTP status codes and
   response conditions count as a successful Squid request to `youtube.com`?
9. Should Squid and the Outline endpoints be reachable only over private
   addresses, or is any narrowly restricted public ingress required?
10. The EU Outline Server uses the official stable image rebuilt with the
    private-target patch. Which Outline
    client version and protected test-key provisioning method should be used?
11. *Resolved:* the approved listener is a local TCP forward on
    `test-client` (127.0.0.1:3128) to EU Squid through RU Outline. Original
    question: for the requested “router IP,” do you mean the test client's default
    gateway, its Outline local proxy listener, or another address? Outline
    clients commonly expose a local proxy rather than a layer-3 router.
12. What cleanup policy is required: delete all test VMs after each run, keep
    them between runs, or use a configurable expiration period?
