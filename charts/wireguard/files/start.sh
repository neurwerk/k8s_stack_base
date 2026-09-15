#!/bin/sh
set -eu

# A container restart can retain its Pod network namespace. Remove the old
# tunnel first; never retain kernel peers or forwarding from an earlier run.
ip link del wg0 2>/dev/null || true
trap 'ip link del wg0 2>/dev/null || true' EXIT
trap 'exit 0' TERM INT

# The Pod sysctl enables IPv4 routing, but no decrypted traffic can enter until
# these deny-by-default rules exist and the new tunnel is configured.
nft -f /etc/gateway/firewall.nft
test "$(cat /proc/sys/net/ipv4/ip_forward)" = 1
ip link add wg0 type wireguard
wg setconf wg0 /etc/gateway/peers.conf
wg set wg0 private-key /run/server-key/privateKey listen-port 51820
ip link set wg0 mtu "$MTU" up
while read -r address; do
    ip route add "$address/32" dev wg0
done < /etc/gateway/addresses

while :; do
    sleep 3600 &
    wait "$!"
done
