"""Disposable Docker-only packet acceptance, never a cluster/host-network test.

Run with uv run python tests/wireguard/packets.py on a capable Linux runner.
Uses ephemeral synthetic keys, the exact chart image and rendered startup rules.
"""

import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
IMAGE = yaml.safe_load((ROOT / "charts/wireguard/values.yaml").read_text())["wireguard"]["image"]


def run(*args, data=None, check=True):
    result = subprocess.run(args, input=data, capture_output=True, text=True, timeout=90)
    if check and result.returncode:
        # Never echo stdin or command arguments: they can contain synthetic keys.
        raise AssertionError(f"{args[0]} failed: {result.stderr}")
    return result


def main():
    prefix = "wg-test-" + uuid.uuid4().hex[:8]
    network = prefix + "-net"
    names = [prefix + "-" + role for role in ("server", "client", "backend", "stranger")]
    server, client, backend, stranger = names
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config = root / "config"
        config.mkdir()
        keydir = root / "key"
        keydir.mkdir()
        try:
            run("docker", "pull", IMAGE)
            run("docker", "network", "create", "--internal", network)
            for name in (client, backend, stranger):
                run("docker", "run", "-d", "--name", name, "--network", network,
                    "--cap-drop=ALL", "--cap-add=NET_ADMIN", "--security-opt=no-new-privileges",
                    "--entrypoint=/bin/sh", IMAGE, "-c", "sleep 3600")

            def ip(name):
                return json.loads(run("docker", "inspect", name).stdout)[0]["NetworkSettings"]["Networks"][network]["IPAddress"]

            def key():
                private = run("docker", "exec", client, "wg", "genkey").stdout
                public = run("docker", "exec", "-i", client, "wg", "pubkey", data=private).stdout.strip()
                return private, public

            private, public = key()
            (keydir / "privateKey").write_text(private)
            (keydir / "privateKey").chmod(0o400)
            # Match the Kubernetes Secret's root-owned 0400 file without adding
            # DAC_OVERRIDE to the runtime. Only this disposable fixture changes.
            run("sudo", "chown", "0:0", str(keydir / "privateKey"))
            peer_private, peer_public = key()
            values = {"wireguard": {"enabled": True, "replicas": 1,
                      "serverKeySecret": "synthetic", "virtualIP": "192.0.2.10",
                      "forgejoServiceIP": ip(backend), "outerSourceCIDRs": [ip(client) + "/32", ip(stranger) + "/32"],
                      "peers": [{"owner": "synthetic", "address": "192.0.2.2", "publicKey": peer_public}]}}

            def start():
                value_file = root / "values.json"
                value_file.write_text(json.dumps(values))
                rendered = run("helm", "template", "wireguard", str(ROOT / "charts/wireguard"), "-f", str(value_file)).stdout
                data = next(doc["data"] for doc in yaml.safe_load_all(rendered) if doc["kind"] == "ConfigMap")
                for name, content in data.items():
                    (config / name).write_text(content)
                run("docker", "run", "-d", "--name", server, "--network", network,
                    "--cap-drop=ALL", "--cap-add=NET_ADMIN", "--security-opt=no-new-privileges",
                    "--read-only", "--sysctl=net.ipv4.ip_forward=1", "-e", "MTU=1280",
                    "-v", f"{config}:/etc/gateway:ro", "-v", f"{keydir}:/run/server-key:ro",
                    "--entrypoint=/bin/sh", IMAGE, "/etc/gateway/start.sh")
                for _ in range(30):
                    probe = run("docker", "exec", server, "/bin/sh", "-c",
                                "ip -o link show wg0 | grep -q '<[^>]*UP[,>]'", check=False)
                    if probe.returncode == 0:
                        return
                    time.sleep(0.2)
                raise AssertionError("Gateway failed startup: " + run("docker", "logs", server).stderr)

            def connect(name, private_key):
                run("docker", "exec", name, "ip", "link", "add", "wg0", "type", "wireguard")
                # /dev/stdin keeps the test private key out of command arguments.
                run("docker", "exec", "-i", name, "wg", "set", "wg0", "private-key", "/dev/stdin",
                    "peer", public, "endpoint", ip(server) + ":51820", "allowed-ips", "0.0.0.0/0", data=private_key)
                run("docker", "exec", name, "ip", "address", "add", "192.0.2.2/32", "dev", "wg0")
                run("docker", "exec", name, "ip", "link", "set", "wg0", "mtu", "1280", "up")
                for dest in ("192.0.2.10", ip(backend), ip(server)):
                    run("docker", "exec", name, "ip", "route", "replace", dest + "/32", "dev", "wg0")
                # Keep the encrypted endpoint on eth0, not the attacker's inner route.
                run("docker", "exec", name, "ip", "route", "replace", ip(server) + "/32", "dev", "eth0")

            def fetch(name, destination, port=443, allowed=False):
                result = run("docker", "exec", name, "wget", "-q", "-T", "2", "-O", "-",
                             f"http://{destination}:{port}/", check=False)
                assert (result.returncode == 0 and result.stdout == "gateway-test\n") == allowed, "unexpected packet result"

            # Alpine BusyBox includes netcat's server mode, not the httpd applet.
            for port in (443, 2222):
                run("docker", "exec", "-d", backend, "/bin/sh", "-c",
                    f"while :; do printf 'HTTP/1.0 200 OK\\r\\nContent-Length: 13\\r\\n\\r\\ngateway-test\\n' | nc -l -p {port}; done")
            fetch(client, ip(backend), allowed=True)
            fetch(client, ip(backend), port=2222, allowed=True)
            start()
            connect(client, peer_private)
            stranger_private, _ = key()
            connect(stranger, stranger_private)
            fetch(client, "192.0.2.10", allowed=True)
            fetch(client, "192.0.2.10", port=2222)
            fetch(client, ip(backend))
            fetch(stranger, "192.0.2.10")
            run("docker", "stop", "-t", "5", server)
            run("docker", "rm", server)
            fetch(client, "192.0.2.10")
            values["wireguard"]["peers"] = []
            start()
            # The replacement may receive a different outer address.
            run("docker", "exec", client, "ip", "route", "replace", ip(server) + "/32", "dev", "eth0")
            run("docker", "exec", client, "wg", "set", "wg0", "peer", public, "endpoint", ip(server) + ":51820")
            fetch(client, "192.0.2.10")
            print("PASS: exact image startup, approved HTTPS transport, denied port/direct address/unapproved peer, stop and empty recovery")
        finally:
            for name in names:
                run("docker", "rm", "-f", name, check=False)
            run("docker", "network", "rm", network, check=False)


if __name__ == "__main__":
    main()
