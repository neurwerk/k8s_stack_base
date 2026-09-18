# Called directly (not in a subshell) so the fresh-sample barrier survives retries.
ceph_health_ready() {
  cluster_json=$(kubectl -n "$POD_NAMESPACE" get cephcluster "$CEPH_CLUSTER" -o json 2>/dev/null) || cluster_json=null
  store_json=$(kubectl -n "$POD_NAMESPACE" get cephobjectstore "$OBJECT_STORE" -o json 2>/dev/null) || store_json=null
  if ! migration_marker=$(jq -ern --argjson cluster "$cluster_json" --argjson store "$store_json" \
    --arg image "$CEPH_IMAGE" --argjson generation "$DAEMON_KEY_GENERATION" '
      def uint: type == "number" and . == floor and . >= 1 and . <= 4294967295;
      def current:
        .metadata.deletionTimestamp == null and
        (.metadata.generation | uint) and
        .status.observedGeneration == .metadata.generation and .status.phase == "Ready";
      def migrated:
        (.keyGeneration | uint) and .keyGeneration >= $generation and
        (.keyCephVersion | (capture("^(?<major>[0-9]+)\\.(?<minor>[0-9]+)\\.(?<patch>[0-9]+)-[0-9]+$") // error("invalid key version")) |
          [.major, .minor, .patch] | map(tonumber)) >= [20, 2, 4];
      ($image | capture(":v(?<version>[0-9]+\\.[0-9]+\\.[0-9]+)(@sha256:[a-f0-9]{64})?$") | .version) as $version |
      select(($generation | uint) and ($cluster | current) and ($store | current)) |
      select($cluster.spec.cephVersion.image == $image and $cluster.status.version.image == $image) |
      select($cluster.status.version.version | test("^" + ($version | gsub("\\."; "\\.")) + "-[0-9]+$")) |
      select($cluster.spec.security.cephx.daemon.keyRotationPolicy == "KeyGeneration" and
        $cluster.spec.security.cephx.daemon.keyGeneration == $generation and
        $cluster.spec.security.cephx.csi.keyType == "aes") |
      select(all(["admin", "mon", "mgr", "osd", "crashCollector", "cephExporter"][];
        . as $component | $cluster.status.cephx[$component] | migrated)) |
      select($store.status.cephx.daemon | migrated) |
      [$cluster.metadata.uid, $cluster.metadata.generation, $store.metadata.uid, $store.metadata.generation] | @json
    ' 2>/dev/null); then
    confirmed_migration=
    echo "Ceph daemon/RGW key migration or current resource status is pending."
    return 1
  fi
  if [ "${confirmed_migration:-}" != "$migration_marker" ]; then
    confirmed_migration=$migration_marker
    health_after=$(date +%s)
    echo "Ceph daemon/RGW keys migrated; waiting for a new health sample."
    return 1
  fi
  if ! health_summary=$(jq -ern --argjson cluster "$cluster_json" --argjson after "$health_after" '
      $cluster.status.ceph |
      (.lastChecked | fromdateiso8601) as $checked |
      select($checked > $after and $checked <= now and now - $checked <= 180) |
      (if has("details") then .details else {} end) as $details |
      select($details | type == "object") |
      if .health == "HEALTH_OK" and ($details | length) == 0 then "HEALTH_OK"
      elif .health == "HEALTH_WARN" and ($details | length) > 0 and
        ($details | to_entries | all(.[];
          .value.severity == "HEALTH_WARN" and
          (.key == "AUTH_INSECURE_ROTATING_SERVICE_KEY_TYPE" or
           .key == "AUTH_INSECURE_CLIENT_KEY_TYPE" or
           .key == "AUTH_INSECURE_KEYS_ALLOWED" or
           .key == "AUTH_INSECURE_KEYS_CREATABLE")))
      then "HEALTH_WARN: accepted compatibility warnings: " + ($details | keys | join(", "))
      else empty end
    ' 2>/dev/null); then
    echo "Ceph health is stale, malformed, or contains blocking warnings/errors."
    return 1
  fi
  echo "$health_summary"
}
