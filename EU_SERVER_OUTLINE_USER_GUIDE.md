# Creating an Outline user on `eu-server`

This guide explains how to create or reuse an Outline access key on
`eu-server` and hand it to the user securely.

## Prerequisites

- `eu-server` is running in the intended GCP project and zone.
- Outline Server has been deployed with
  `.venv/bin/eu-server-agent deploy-outline`.
- The Outline health check passes:
  `.venv/bin/eu-server-test-agent outline`.
- `config/test.env` is configured, and `gcloud` has valid operator
  credentials with IAP SSH access to `eu-server`.

## Create or retrieve a user's access URL

Run the command from the repository root, using a unique, non-secret display
name for the user:

```sh
.venv/bin/eu-server-agent create-outline-user --name USER_NAME
```

The command looks for an existing Outline access key with that name. If one
exists, it reuses it; if not, it creates one. It prints the resulting
`ss://` access URL to standard output. The URL is a credential: anyone who
obtains it can use the Outline service as that user.

Handle the output only in a trusted terminal and transfer it using the
organization's approved protected secret store or another approved secure
handoff. Do not redirect it to a file, paste it into tickets or chat, include
it in shell command arguments, or capture it in CI logs or terminal
recordings. The command does not save a separate copy; Outline Server retains
its access-key record in its protected state directory on `eu-server`.

Use a non-secret user/display name. Do not put passwords or other credentials
in `--name`.

## Keep the access URL in a shell environment variable

To capture the URL without printing it in the terminal, assign the command's
standard output to an exported variable in the current Bash session:

```sh
export OUTLINE_ACCESS_URL="$(.venv/bin/eu-server-agent create-outline-user --name USER_NAME)"
```

The URL is not part of the command text or shell history, but it remains
available to child processes of this shell and may be visible to processes
running under the same account or to administrators. Use this only in a
trusted, short-lived shell session. Do not put the command in a script,
`.bashrc`, CI configuration, or other persistent environment setup. Do not
print the variable with `echo`, enable shell tracing with `set -x`, or save it
to a file.

To check that a value was captured without revealing it:

```sh
if [[ "${OUTLINE_ACCESS_URL:-}" == ss://* ]]; then
  printf '%s\n' "Outline access URL is set."
else
  printf '%s\n' "Outline access URL was not created." >&2
  return 1
fi
```

When finished, remove it from the current shell:

```sh
unset OUTLINE_ACCESS_URL
```

## Automated test-environment check

`test_env_host` deploys Outline and checks its authorized management API
before creating or reusing the named test user. It passes the generated URL
in memory to `eu-server-test-agent`, which starts a temporary pinned
Shadowsocks client and sends an HTTP request through the key. The orchestrator
does not print or log the URL. A failed user creation or connection check
causes the overall workflow to fail.
