#!/usr/bin/env bash
set -euo pipefail

tag="${1:?release tag is required}"
expected_ref="${2:-HEAD}"
allowed_signer="${PLATFORM_RELEASE_ALLOWED_SIGNER:-}"
public_key_file="${PLATFORM_RELEASE_PUBLIC_KEY_FILE:-release/trust/platform-release.sshpub}"
expected_fingerprint='SHA256:+rDcofrsfRE3ElJJxnUVoB3gmoEzZJUrisDqLZMHimw'

if [[ ! "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  printf '%s\n' "invalid release tag: $tag" >&2
  exit 1
fi
if [[ -z "$allowed_signer" ]]; then
  printf '%s\n' 'PLATFORM_RELEASE_ALLOWED_SIGNER is required' >&2
  exit 1
fi
if [[ "$allowed_signer" == *$'\n'* || "$allowed_signer" == *$'\r'* ]]; then
  printf '%s\n' 'PLATFORM_RELEASE_ALLOWED_SIGNER must be exactly one line' >&2
  exit 1
fi

read -r principal namespace algorithm public_key extra <<< "$allowed_signer"
test "$principal" = platform-release
test "$namespace" = 'namespaces="git"'
test "$algorithm" = ssh-ed25519
test -n "$public_key"
test -z "${extra:-}"

umask 077
key_file="$(mktemp)"
trust_file="$(mktemp)"
trap 'rm -f "$key_file" "$trust_file"' EXIT

printf 'ssh-ed25519 %s\n' "$public_key" > "$key_file"
fingerprint="$(ssh-keygen -lf "$key_file" -E sha256 | awk '{print $2}')"
test "$fingerprint" = "$expected_fingerprint"

read -r committed_algorithm committed_key _ \
  < "$public_key_file"
test "$committed_algorithm" = ssh-ed25519
test "$committed_key" = "$public_key"

# Never copy the configuration variable: emit one canonical line from validated fields.
printf 'platform-release namespaces="git" ssh-ed25519 %s\n' \
  "$public_key" > "$trust_file"
git -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile="$trust_file" \
  verify-tag -- "$tag"
test "$(git rev-parse --verify "$tag^{commit}")" = \
  "$(git rev-parse --verify "$expected_ref^{commit}")"
