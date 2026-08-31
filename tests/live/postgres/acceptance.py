#!/usr/bin/env python3
"""Run an explicitly authorized disposable postgres-operations acceptance test."""

from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


CONFIRMATION = "I_CONFIRM_DISPOSABLE_POSTGRES_ACCEPTANCE"
NAMESPACE_PREFIX = "postgres-operations-acceptance-"
CA_CONFIGMAP = "infra-openbao-ca-bundle"
IDENTITY_CONFIGMAP = "neurwerk-stack-identity"
IDENTITY_NAMESPACE = "flux-system"
ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "charts/postgres/operations"
DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")


class AcceptanceError(RuntimeError):
    """Report an acceptance failure without command output or credentials."""


@dataclass(frozen=True)
class Config:
    kubeconfig: Path
    context: str
    client: str
    storage_class: str


@dataclass(frozen=True)
class Passwords:
    admin: str
    documentdb: str
    dify: str
    langfuse: str
    librechat_rag: str


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not value or value != value.strip():
        raise AcceptanceError(f"{name} must be supplied without surrounding whitespace")
    return value


def load_config(environ: Mapping[str, str]) -> Config:
    """Validate every guard before contacting or mutating Kubernetes."""
    if environ.get("POSTGRES_LIVE_ACCEPTANCE_CONFIRM") != CONFIRMATION:
        raise AcceptanceError(
            f"POSTGRES_LIVE_ACCEPTANCE_CONFIRM must equal exactly {CONFIRMATION}"
        )
    kubeconfig = Path(_required(environ, "POSTGRES_LIVE_ACCEPTANCE_KUBECONFIG"))
    context = _required(environ, "POSTGRES_LIVE_ACCEPTANCE_EXPECTED_CONTEXT")
    client = _required(environ, "POSTGRES_LIVE_ACCEPTANCE_EXPECTED_CLIENT")
    storage_class = _required(environ, "POSTGRES_LIVE_ACCEPTANCE_STORAGE_CLASS")
    if not kubeconfig.is_absolute() or not kubeconfig.is_file():
        raise AcceptanceError(
            "POSTGRES_LIVE_ACCEPTANCE_KUBECONFIG must be an existing absolute file"
        )
    if not DNS_LABEL_RE.fullmatch(storage_class):
        raise AcceptanceError("POSTGRES_LIVE_ACCEPTANCE_STORAGE_CLASS is invalid")
    return Config(kubeconfig, context, client, storage_class)


def _run(
    command: list[str],
    *,
    stdin: str | None = None,
    timeout: int = 30,
    check: bool = True,
) -> str:
    try:
        result = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise AcceptanceError(f"{command[0]} failed or timed out; output redacted") from exc
    if check and result.returncode != 0:
        raise AcceptanceError(f"{command[0]} failed; output redacted")
    return result.stdout


def kubectl(config: Config, *arguments: str) -> list[str]:
    return [
        "kubectl",
        f"--kubeconfig={config.kubeconfig}",
        f"--context={config.context}",
        *arguments,
    ]


def acceptance_namespace() -> str:
    return f"{NAMESPACE_PREFIX}{secrets.token_hex(4)}"


def generate_passwords() -> Passwords:
    def password() -> str:
        return f"Aa1!{secrets.token_urlsafe(24)}"

    return Passwords(password(), password(), password(), password(), password())


def values_json(config: Config, passwords: Passwords) -> str:
    """Keep synthetic credentials in stdin rather than process arguments or files."""
    return json.dumps(
        {
            "postgresOperations": {
                "persistence": {
                    "size": "1Gi",
                    "storageClassName": config.storage_class,
                }
            },
            "postgresOperationsSecrets": {
                "adminPassword": passwords.admin,
                "documentdbPassword": passwords.documentdb,
                "difyPassword": passwords.dify,
                "langfusePassword": passwords.langfuse,
                "librechatRagPassword": passwords.librechat_rag,
            },
        }
    )


def helm_command(config: Config, namespace: str) -> list[str]:
    return [
        "helm",
        "upgrade",
        "--install",
        "postgres-operations",
        str(CHART),
        f"--kubeconfig={config.kubeconfig}",
        f"--kube-context={config.context}",
        f"--namespace={namespace}",
        "--timeout=15m",
        "--history-max=3",
        "--values=-",
    ]


def resource_status(config: Config, namespace: str) -> str:
    output = _run(
        kubectl(
            config,
            "get",
            "pod",
            "postgres-operations-0",
            f"--namespace={namespace}",
            "--output=json",
        ),
        timeout=10,
        check=False,
    )
    if not output:
        pod_summary = "pod=not-created"
    else:
        try:
            pod = json.loads(output)
        except json.JSONDecodeError:
            pod_summary = "pod=status-unavailable"
        else:
            statuses = pod.get("status", {}).get("containerStatuses", [])
            ready = any(status.get("ready") is True for status in statuses)
            restarts = sum(status.get("restartCount", 0) for status in statuses)
            reason = "none"
            if statuses:
                state = statuses[0].get("state", {})
                for state_name in ("waiting", "terminated", "running"):
                    if state_name in state:
                        reason = state[state_name].get("reason", state_name)
                        break
            pod_summary = (
                f"pod={pod.get('status', {}).get('phase', 'Unknown')} "
                f"ready={str(ready).lower()} reason={reason} restarts={restarts}"
            )

    job_output = _run(
        kubectl(
            config,
            "get",
            "job",
            "postgres-operations-provision",
            f"--namespace={namespace}",
            "--output=json",
        ),
        timeout=10,
        check=False,
    )
    if not job_output:
        return f"{pod_summary} hook=not-created"
    try:
        job = json.loads(job_output)
    except json.JSONDecodeError:
        return f"{pod_summary} hook=status-unavailable"
    status = job.get("status", {})
    hook = (
        f"hook=active:{status.get('active', 0)}"
        f"/succeeded:{status.get('succeeded', 0)}"
        f"/failed:{status.get('failed', 0)}"
    )
    return f"{pod_summary} {hook}"


def run_helm(
    command: list[str],
    values: str,
    phase: str,
    config: Config,
    namespace: str,
) -> None:
    """Run Helm with redacted output and visible bounded progress."""
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError as exc:
        raise AcceptanceError("helm was not found") from exc
    try:
        if process.stdin is None:
            raise AcceptanceError("helm stdin is unavailable")
        process.stdin.write(values)
        process.stdin.close()
        elapsed = 0
        while process.poll() is None:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                elapsed += 30
                print(
                    f"WAIT {phase}: {resource_status(config, namespace)} "
                    f"({elapsed}s elapsed)",
                    flush=True,
                )
        if process.returncode != 0:
            raise AcceptanceError("helm failed; output redacted")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def verify_target(config: Config) -> None:
    current_context = _run(
        ["kubectl", f"--kubeconfig={config.kubeconfig}", "config", "current-context"]
    ).strip()
    if current_context != config.context:
        raise AcceptanceError("kubeconfig current context does not match the expected context")

    identity = json.loads(
        _run(
            kubectl(
                config,
                "get",
                "configmap",
                IDENTITY_CONFIGMAP,
                f"--namespace={IDENTITY_NAMESPACE}",
                "--output=json",
            )
        )
    )
    if identity.get("data", {}).get("client") != config.client:
        raise AcceptanceError("cluster client identity does not match the expected client")

    _run(kubectl(config, "get", "storageclass", config.storage_class, "--output=name"))


def generate_tls(namespace: str) -> tuple[str, str, str]:
    """Generate a short-lived synthetic CA and service certificate for the test."""
    dns_names = (
        "localhost",
        "postgres-operations",
        f"postgres-operations.{namespace}",
        f"postgres-operations.{namespace}.svc",
        f"postgres-operations.{namespace}.svc.cluster.local",
    )
    with tempfile.TemporaryDirectory(prefix="postgres-acceptance-") as directory:
        temporary = Path(directory)
        ca_key = temporary / "ca.key"
        ca_cert = temporary / "ca.crt"
        tls_key = temporary / "tls.key"
        tls_request = temporary / "tls.csr"
        tls_cert = temporary / "tls.crt"
        extensions = temporary / "tls.ext"
        extensions.write_text(
            "\n".join(
                (
                    "basicConstraints=critical,CA:FALSE",
                    "keyUsage=critical,digitalSignature,keyEncipherment",
                    "extendedKeyUsage=serverAuth",
                    "subjectAltName="
                    + ",".join(f"DNS:{name}" for name in dns_names)
                    + ",IP:127.0.0.1",
                )
            )
            + "\n",
            encoding="ascii",
        )
        _run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(ca_key),
                "-out",
                str(ca_cert),
                "-days",
                "2",
                "-subj",
                "/CN=Neurwerk Disposable Acceptance CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
            ]
        )
        _run(
            [
                "openssl",
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(tls_key),
                "-out",
                str(tls_request),
                "-subj",
                "/CN=postgres-operations",
            ]
        )
        _run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(tls_request),
                "-CA",
                str(ca_cert),
                "-CAkey",
                str(ca_key),
                "-CAcreateserial",
                "-out",
                str(tls_cert),
                "-days",
                "2",
                "-sha256",
                "-extfile",
                str(extensions),
            ]
        )
        return (
            ca_cert.read_text(encoding="ascii"),
            tls_cert.read_text(encoding="ascii"),
            tls_key.read_text(encoding="ascii"),
        )


def create_namespace(config: Config, namespace: str) -> None:
    namespace_manifest = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
            "labels": {"neurwerk.com/live-acceptance": "postgres-operations"},
        },
    }
    _run(
        kubectl(config, "create", "--filename=-"),
        stdin=json.dumps(namespace_manifest),
    )


def create_tls_resources(
    config: Config,
    namespace: str,
    ca: str,
    certificate: str,
    private_key: str,
) -> None:
    ca_manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": CA_CONFIGMAP, "namespace": namespace},
        "data": {"ca.crt": ca},
    }
    _run(kubectl(config, "create", "--filename=-"), stdin=json.dumps(ca_manifest))
    secret_manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "postgres-operations-documentdb-tls",
            "namespace": namespace,
        },
        "type": "kubernetes.io/tls",
        "stringData": {"tls.crt": certificate, "tls.key": private_key},
    }
    _run(kubectl(config, "create", "--filename=-"), stdin=json.dumps(secret_manifest))


def verify_install(config: Config, namespace: str) -> None:
    _run(
        kubectl(
            config,
            "rollout",
            "status",
            "statefulset/postgres-operations",
            f"--namespace={namespace}",
            "--timeout=2m",
        ),
        timeout=130,
    )
    stateful_set = json.loads(
        _run(
            kubectl(
                config,
                "get",
                "statefulset",
                "postgres-operations",
                f"--namespace={namespace}",
                "--output=json",
            )
        )
    )
    if stateful_set.get("status", {}).get("readyReplicas") != 1:
        raise AcceptanceError("the disposable postgres-operations StatefulSet is not Ready")

    pvc = json.loads(
        _run(
            kubectl(
                config,
                "get",
                "pvc",
                "data-postgres-operations-0",
                f"--namespace={namespace}",
                "--output=json",
            )
        )
    )
    if pvc.get("status", {}).get("phase") != "Bound":
        raise AcceptanceError("the disposable postgres-operations PVC is not Bound")

    job = json.loads(
        _run(
            kubectl(
                config,
                "get",
                "job",
                "postgres-operations-provision",
                f"--namespace={namespace}",
                "--output=json",
            )
        )
    )
    if job.get("status", {}).get("succeeded") != 1:
        raise AcceptanceError("the disposable provisioning Job did not succeed")


def run_acceptance(config: Config) -> None:
    verify_target(config)
    print("PASS expected Kubernetes context, client identity, and storage", flush=True)
    namespace = acceptance_namespace()
    created = False
    succeeded = False
    try:
        create_namespace(config, namespace)
        created = True
        ca, certificate, private_key = generate_tls(namespace)
        create_tls_resources(config, namespace, ca, certificate, private_key)
        print("PASS created isolated namespace and synthetic TLS identity", flush=True)
        values = values_json(config, generate_passwords())

        run_helm(
            helm_command(config, namespace),
            values,
            "clean install",
            config,
            namespace,
        )
        verify_install(config, namespace)
        print(
            "PASS clean install, user creation, authentication, and read/write verification",
            flush=True,
        )

        run_helm(
            helm_command(config, namespace),
            values,
            "idempotent upgrade",
            config,
            namespace,
        )
        verify_install(config, namespace)
        print("PASS idempotent upgrade and existing-user reconciliation", flush=True)
        succeeded = True
    finally:
        if created and succeeded:
            _run(
                kubectl(
                    config,
                    "delete",
                    "namespace",
                    namespace,
                    "--wait=true",
                    "--timeout=10m",
                ),
                timeout=620,
            )
            print("PASS removed disposable namespace and PVC", flush=True)
        elif created:
            print(
                f"PRESERVED diagnostic namespace {namespace}; delete it after inspection",
                file=sys.stderr,
                flush=True,
            )


def terminate_acceptance(_signum: int, _frame: object) -> None:
    raise AcceptanceError("acceptance terminated")


def main(environ: Mapping[str, str] | None = None) -> int:
    signal.signal(signal.SIGTERM, terminate_acceptance)
    try:
        run_acceptance(load_config(os.environ if environ is None else environ))
    except (AcceptanceError, json.JSONDecodeError, KeyboardInterrupt) as exc:
        message = str(exc) or "acceptance interrupted"
        print(f"ERROR: {message}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
