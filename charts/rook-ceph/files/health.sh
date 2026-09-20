# Called directly (not in a subshell) so the fresh-sample barrier survives retries.
ceph_health_ready() {
  cluster_json=$(kubectl -n "$POD_NAMESPACE" get cephcluster "$CEPH_CLUSTER" -o json 2>/dev/null) || cluster_json=null
  store_json=$(kubectl -n "$POD_NAMESPACE" get cephobjectstore "$OBJECT_STORE" -o json 2>/dev/null) || store_json=null
  if ! status_marker=$(jq -ern --argjson cluster "$cluster_json" --argjson store "$store_json" \
    --arg image "$CEPH_IMAGE" '
      def uint: type == "number" and . == floor and . >= 1 and . <= 4294967295;
      def current:
        .metadata.deletionTimestamp == null and
        (.metadata.generation | uint) and
        .status.observedGeneration == .metadata.generation and .status.phase == "Ready";
      ($image | capture(":v(?<version>[0-9]+\\.[0-9]+\\.[0-9]+)(@sha256:[a-f0-9]{64})?$") | .version) as $version |
      select(($cluster | current) and ($store | current)) |
      select($cluster.spec.cephVersion.image == $image and $cluster.status.version.image == $image) |
      select($cluster.status.version.version | test("^" + ($version | gsub("\\."; "\\.")) + "-[0-9]+$")) |
      [$cluster.metadata.uid, $cluster.metadata.generation, $store.metadata.uid, $store.metadata.generation] | @json
    ' 2>/dev/null); then
    confirmed_status=
    echo "Ceph cluster/object store current resource status is pending."
    return 1
  fi
  if [ "${confirmed_status:-}" != "$status_marker" ]; then
    confirmed_status=$status_marker
    health_after=$(date +%s)
    echo "Ceph cluster/object store status is ready; waiting for a new health sample."
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
        ($details | all(.severity == "HEALTH_WARN")) then "HEALTH_WARN"
      else empty end
    ' 2>/dev/null); then
    echo "Ceph health is stale, malformed, or contains blocking errors."
    return 1
  fi
  echo "$health_summary"
}
