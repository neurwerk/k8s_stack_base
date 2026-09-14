#!/bin/bash
set -euo pipefail
umask 077
trap 'printf "Forgejo initialization failed; app startup is blocked.\n" >&2' ERR

# Kubernetes may retry this container; each attempt has a 300-second deadline.
# Never print CLI output: an upstream error may include confidential arguments.
cli() {
  /app/gitea/gitea --work-path /data --config /run/forgejo/app.ini "$@"
}

mkdir -p /data/git /data/custom /data/ssh /data/repositories
for key in dbPassword secretKey internalToken oauth2JwtSecret lfsJwtSecret; do
  test -s "/run/secrets/forgejo/$key"
done
for key in oidcClientSecret adminPassword; do
  test -s "/run/bootstrap/$key"
done
# Forgejo otherwise silently replaces malformed JWT secrets with random keys.
for key in oauth2JwtSecret lfsJwtSecret; do
  test "$(wc -c < "/run/secrets/forgejo/$key")" -eq 43
  LC_ALL=C grep -Eq '^[A-Za-z0-9_-]{43}$' "/run/secrets/forgejo/$key"
done

# Rebuild from an empty file on EVERY attempt; removed settings cannot linger.
# Native URI settings load confidential fields directly from the Secret mount.
: > /run/forgejo/empty.ini
environment-to-ini --config /run/forgejo/empty.ini --out /run/forgejo/app.ini >/dev/null 2>&1
# Identity-provider trust is independent of Forgejo's serving certificate.
cat /etc/ssl/certs/ca-certificates.crt > /run/forgejo/ca-bundle.pem
if test -n "${OIDC_CA_FILE:-}"; then
  test -s "$OIDC_CA_FILE"
  printf '\n' >> /run/forgejo/ca-bundle.pem
  cat "$OIDC_CA_FILE" >> /run/forgejo/ca-bundle.pem
fi

if ! test -s /data/ssh/forgejo.ed25519; then
  ssh-keygen -q -t ed25519 -N '' -f /data/ssh/forgejo.ed25519 >/dev/null 2>&1
fi

# Migrate with the identical application image before serving any requests.
cli migrate >/dev/null 2>&1

# Parse the documented delimiter and exact name, never a substring/regex name.
# Unexpected sources are not deleted: fail closed for deliberate operator review.
auth_id() {
  cli admin auth list --vertical-bars | awk -F '|' '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    { for (i=1; i<=NF; i++) $i=trim($i) }
    $1=="ID" && $2=="Name" && $3=="Type" && $4=="Enabled" { header=1; next }
    header && NF {
      if (NF!=4 || $1 !~ /^[0-9]+$/ || $2!="keycloak" || $3!="OAuth2" || $4!="true") exit 1
      id=$1; count++
    }
    END { if (!header || count>1) exit 1; if (count==1) print id }
  '
}

id=$(auth_id 2>/dev/null)
if test "$FORGEJO__SERVICE__ENABLE_INTERNAL_SIGNIN" = true; then
  # Recovery is only for an already provisioned installation. Do not trigger
  # remote discovery by registering/updating the existing OAuth provider.
  test -n "$id"
else
  oauth_args=(--name keycloak --provider openidConnect --key forgejo
    --auto-discover-url "$OIDC_DISCOVERY_URL"
    --scopes openid --scopes profile --scopes email
    --required-claim-name forgejo_roles --required-claim-value forgejo-user
    --group-claim-name forgejo_roles --admin-group forgejo-admin
    --allow-username-change=false --group-team-map '' --restricted-group ''
    --group-team-map-removal=false --attribute-ssh-public-key ''
    --quota-group-claim-name '' --quota-group-map '' --quota-group-map-removal=false)
  operation=add-oauth
  if test -n "$id"; then
    operation=update-oauth
    oauth_args+=(--id "$id")
  fi
  # xargs reads one literal argument from the mounted file. Secret bytes never
  # enter shell expansion, generated scripts, Helm values, or logs.
  xargs -0 /app/gitea/gitea --work-path /data --config /run/forgejo/app.ini \
    admin auth "$operation" "${oauth_args[@]}" --secret \
    < /run/bootstrap/oidcClientSecret >/dev/null 2>&1
  verified_id=$(auth_id 2>/dev/null)
  test -n "$verified_id"
  if test -n "$id"; then
    test "$verified_id" = "$id"
  fi
fi

recovery_id() {
  cli admin user list | awk '
    $1=="ID" && $2=="Username" && $3=="Email" && $4=="IsActive" && $5=="IsAdmin" && $6=="2FA" { header=1; next }
    header && NF {
      if (NF!=6 || $1 !~ /^[0-9]+$/) exit 1
      if ($2=="forgejo-recovery") {
        if ($4!="true" || $5!="true") exit 1
        id=$1; count++
      }
    }
    END { if (!header || count>1) exit 1; if (count==1) print id }
  '
}

recovery=$(recovery_id 2>/dev/null)
if test -z "$recovery"; then
  # Never create or promote an account while emergency local login is enabled.
  test "$FORGEJO__SERVICE__ENABLE_INTERNAL_SIGNIN" = false
  xargs -0 /app/gitea/gitea --work-path /data --config /run/forgejo/app.ini \
    admin user create --username forgejo-recovery --email "$RECOVERY_EMAIL" \
    --admin --must-change-password=false --password \
    < /run/bootstrap/adminPassword >/dev/null 2>&1
fi
verified_recovery=$(recovery_id 2>/dev/null)
test -n "$verified_recovery"
printf 'Forgejo initialization verified.\n'
