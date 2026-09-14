"""Closed, local-only subset of Kustomize/Flux inputs for access planning."""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
from pathlib import Path

import yaml

if __package__:
    from .check_application_access import PlanError, PlanLoader, _mapping
else:
    from check_application_access import PlanError, PlanLoader, _mapping


UNKNOWN = object()
MISSING = object()
INVALID = object()
# Reviewed, unmodified generated Flux v2.9.4 controller bundle (no client facts).
BOOTSTRAP_SHA256 = "97da4654bc11de5637d7f506b0a73707e1e6a1f7a8e9fa8e307063c0b9befa1f"
# Only these fixed wrapper dependencies need upstream ingress defaults.
VENDOR_DEFAULTS = {"langfuse", "kube-prometheus-stack"}
RESOURCE_APIS = {
    "v1": {"Namespace", "ConfigMap", "ServiceAccount", "Service", "ResourceQuota"},
    "apps/v1": {"Deployment"},
    "networking.k8s.io/v1": {"NetworkPolicy"},
    "rbac.authorization.k8s.io/v1": {"Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"},
    "apiextensions.k8s.io/v1": {"CustomResourceDefinition"},
    "external-secrets.io/v1": {"ExternalSecret", "SecretStore", "ClusterSecretStore"},
    "external-secrets.io/v1beta1": {"ExternalSecret", "SecretStore", "ClusterSecretStore"},
    "helm.toolkit.fluxcd.io/v2": {"HelmRelease"},
    "source.toolkit.fluxcd.io/v1": {"GitRepository"},
    "kustomize.toolkit.fluxcd.io/v1": {"Kustomization"},
}


def mapping(value: object, required: str = "", optional: str = "") -> dict:
    return _mapping(value, "composition", tuple(required.split()), tuple(optional.split()))


def text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise PlanError("composition: expected a nonempty unpadded string")
    return value


def sequence(value: object) -> list:
    if type(value) is not list:
        raise PlanError("composition: expected a list")
    return value


def management_annotations(value: object) -> None:
    if type(value) is not dict or any(type(k) is not str or type(v) is not str for k, v in value.items()):
        raise PlanError("composition: annotations must be a string mapping")
    for key, supported in (("kustomize.toolkit.fluxcd.io/ssa", "Override"),
                           ("kustomize.toolkit.fluxcd.io/reconcile", "enabled")):
        if key in value and value[key] != supported:
            raise PlanError("composition: apply-suppressed or non-Override management annotations unsupported")


def local(root: Path, base: Path, name: str) -> Path:
    name = text(name)
    if Path(name).is_absolute() or ":" in name or "\\" in name or "?" in name or "#" in name or any(ord(c) < 32 for c in name):
        raise PlanError("composition: only local paths are supported")
    try:
        path = (base / name).resolve(strict=True)
        if not path.is_relative_to(root):
            raise PlanError("composition: path escapes supplied source root")
        if any(p.is_symlink() for p in (base / name, *(base / name).parents) if p.is_relative_to(root)):
            raise PlanError("composition: symlink payloads unsupported")
        if ".git" in path.relative_to(root).parts or not (path.is_file() or path.is_dir()):
            raise PlanError("composition: unsupported local source path")
        for parent in (path if path.is_dir() else path.parent, *path.parents):
            if parent.is_relative_to(root) and (parent / ".sourceignore").exists():
                raise PlanError("composition: source ignore files unsupported")
        return path
    except (OSError, RuntimeError):
        raise PlanError("composition: missing or invalid local path") from None


def parse(value: str) -> list:
    try:
        return list(yaml.load_all(value, Loader=PlanLoader))
    except (yaml.YAMLError, RecursionError, ValueError, OverflowError) as error:
        if isinstance(error, PlanError):
            raise
        raise PlanError("composition: invalid or unsupported YAML; contents withheld") from None


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise PlanError("composition: cannot read local UTF-8 input") from None


def document(value: str) -> dict:
    docs = parse(value)
    if len(docs) != 1 or type(docs[0]) is not dict:
        raise PlanError("composition: expected one YAML mapping")
    return docs[0]


def source(ref: object, namespace: str) -> str:
    ref = mapping(ref, "kind name", "namespace")
    if (ref["kind"] != "GitRepository" or ref["name"] not in ("k8s-stack", "flux-system")
            or ref.get("namespace", namespace) != "flux-system"):
        raise PlanError("composition: unsupported sourceRef")
    return ref["name"]


class Composition:
    """Selected inventory, with lazy ConfigMap file entries and no Secret resolver."""

    def __init__(self, client: Path, platform: Path, cluster: str, platform_snapshot=None):
        self.roots = {"flux-system": client.resolve(), "k8s-stack": platform.resolve()}
        self.platform_snapshot = platform_snapshot
        self.client_inputs: set[Path] = set()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", cluster):
            raise PlanError("composition: invalid cluster selector")
        self.entry = local(self.roots["flux-system"], client, f"clusters/{cluster}")
        self.objects: dict[tuple, dict] = {}
        self.bootstrap: set[int] = set()
        self.active: set[Path] = set()
        self.flux_paths: set[Path] = {self.entry}
        self.build("flux-system", self.entry)

    def read_bytes(self, path: Path, owner: str) -> bytes:
        if owner == "k8s-stack" and self.platform_snapshot is not None:
            return self.platform_snapshot.read(path)
        try:
            data = path.read_bytes()
            if owner == "flux-system":
                self.client_inputs.add(path)
            return data
        except OSError:
            raise PlanError("composition: cannot read local input") from None

    def read(self, path: Path, owner: str) -> str:
        try:
            return self.read_bytes(path, owner).decode("utf-8")
        except UnicodeError:
            raise PlanError("composition: cannot read local UTF-8 input") from None

    def build(self, owner: str, path: Path) -> list[dict]:
        if path in self.active:
            raise PlanError("composition: local resource cycle")
        self.active.add(path)
        try:
            if path.is_dir():
                config = document(self.read(local(self.roots[owner], path, "kustomization.yaml"), owner))
                mapping(config, "apiVersion kind", "resources namespace generatorOptions configMapGenerator commonLabels commonAnnotations")
                if config["apiVersion"] != "kustomize.config.k8s.io/v1beta1" or config["kind"] != "Kustomization":
                    raise PlanError("composition: unsupported local Kustomization")
                for field in ("commonLabels", "commonAnnotations"):
                    if field in config and (type(config[field]) is not dict or any(
                            type(k) is not str or type(v) is not str for k, v in config[field].items())):
                        raise PlanError("composition: invalid common metadata")
                management_annotations(config.get("commonAnnotations", {}))
                items = []
                for item in sequence(config.get("resources", [])):
                    items.extend(self.build(owner, local(self.roots[owner], path, item)))
                options = mapping(config.get("generatorOptions", {}), "", "disableNameSuffixHash labels annotations")
                management_annotations(options.get("annotations", {}))
                generators = sequence(config.get("configMapGenerator", []))
                if generators and options.get("disableNameSuffixHash") is not True:
                    raise PlanError("composition: ConfigMap generators require stable names")
                for gen in generators:
                    gen = mapping(gen, "name files", "namespace")
                    data = {}
                    for item in sequence(gen["files"]):
                        item = text(item)
                        key, filename = item.split("=", 1) if "=" in item else (Path(item).name, item)
                        if not re.fullmatch(r"[-._a-zA-Z0-9]+", key) or key in data:
                            raise PlanError("composition: invalid or duplicate generator key")
                        generated = local(self.roots[owner], path, filename)
                        if owner == "k8s-stack" and self.platform_snapshot is not None:
                            self.platform_snapshot.require(generated)
                        data[key] = (owner, generated)
                    items.append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
                        "name": text(gen["name"]), "namespace": gen.get("namespace", "default")}, "data": data})
                if "namespace" in config:
                    namespace = text(config["namespace"])
                    for item in items:
                        if item["kind"] not in ("Namespace", "CustomResourceDefinition", "ClusterRole", "ClusterRoleBinding", "ClusterSecretStore"):
                            item["metadata"]["namespace"] = namespace
                if path == self.entry or path in self.flux_paths:
                    self.collect(items)
                return items
            raw = self.read_bytes(path, owner)
            bootstrap = path == self.entry / "flux-system/gotk-components.yaml"
            if bootstrap and hashlib.sha256(raw).hexdigest() != BOOTSTRAP_SHA256:
                raise PlanError("composition: untrusted Flux bootstrap bundle")
            try:
                docs = [doc for doc in parse(raw.decode("utf-8")) if doc is not None]
            except UnicodeError:
                raise PlanError("composition: cannot read local UTF-8 input") from None
            for doc in docs:
                if type(doc) is not dict:
                    raise PlanError("composition: expected Kubernetes mapping")
                text(doc.get("apiVersion"))
                text(doc.get("kind"))
                if type(doc.get("metadata")) is not dict:
                    raise PlanError("composition: resource metadata required")
                text(doc["metadata"].get("name"))
                # Generated Flux controllers are infrastructure, not browser endpoints.
                if bootstrap:
                    self.bootstrap.add(id(doc))
            return docs
        finally:
            self.active.remove(path)

    def collect(self, items: list[dict]) -> None:
        pending = []
        for item in items:
            kind, meta = item["kind"], item["metadata"]
            management_annotations(meta.get("annotations", {}))
            if kind not in RESOURCE_APIS.get(item["apiVersion"], set()):
                raise PlanError("composition: unsupported selected resource API/kind")
            identity = (item["apiVersion"].split("/")[0] if "/" in item["apiVersion"] else "", kind,
                        text(meta.get("namespace", "default")), text(meta["name"]))
            if identity in self.objects:
                raise PlanError("composition: duplicate resource identity")
            self.objects[identity] = item
            if kind == "Kustomization":
                if item["apiVersion"] != "kustomize.toolkit.fluxcd.io/v1":
                    raise PlanError("composition: unsupported Flux Kustomization version")
                spec = mapping(item.get("spec"), "path sourceRef", "interval retryInterval timeout wait prune dependsOn healthChecks healthCheckExprs deletionPolicy force suspend serviceAccountName")
                if spec.get("suspend", False) is not False:
                    raise PlanError("composition: suspended selection unsupported, not absent")
                owner = source(spec["sourceRef"], identity[2])
                target = local(self.roots[owner], self.roots[owner], spec["path"])
                if not target.is_dir():
                    raise PlanError("composition: Flux path must select a local directory")
                if owner == "flux-system" and meta["name"] == "flux-system" and target == self.entry:
                    continue
                if target in self.flux_paths:
                    raise PlanError("composition: overlapping or cyclic Flux selection")
                self.flux_paths.add(target)
                pending.append((owner, target))
            elif kind in ("HelmRelease", "ConfigMap", "GitRepository"):
                continue
            elif kind in ("Namespace", "NetworkPolicy", "ServiceAccount", "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding", "ExternalSecret", "SecretStore", "ClusterSecretStore"):
                continue
            elif id(item) in self.bootstrap and kind in ("Deployment", "Service", "CustomResourceDefinition", "ResourceQuota"):
                if kind != "CustomResourceDefinition" and meta.get("namespace") != "flux-system":
                    raise PlanError("composition: bootstrap namespace transform unsupported")
                continue
            else:
                raise PlanError("composition: unsupported selected resource kind")
        for owner, target in pending:
            self.build(owner, target)

    def secret_shape(self, namespace: str, name: str, key: str):
        """Declared producer write scope only, never Secret values or sync evidence."""
        producers = []
        for obj in self.objects.values():
            if obj["kind"] != "ExternalSecret" or obj["metadata"].get("namespace", "default") != namespace:
                continue
            spec = obj.get("spec", {})
            target = spec.get("target", {}) if type(spec) is dict else None
            if type(target) is not dict:
                return UNKNOWN
            if target.get("name", obj["metadata"]["name"]) == name:
                producers.append(target)
        if len(producers) != 1:
            return UNKNOWN
        try:
            target = mapping(producers[0], "name creationPolicy template", "deletionPolicy")
            template = mapping(target["template"], "engineVersion data", "type metadata mergePolicy")
            if target["creationPolicy"] != "Owner" or template["engineVersion"] != "v2" or template.get("mergePolicy", "Replace") != "Replace" or template.get("type", "Opaque") != "Opaque":
                return UNKNOWN
            metadata = mapping(template.get("metadata", {}), "", "labels annotations")
            for fields in metadata.values():
                if type(fields) is not dict or any(type(k) is not str or type(v) is not str or "{{" in k + v or "}}" in k + v for k, v in fields.items()):
                    return UNKNOWN
            data = template["data"]
            if type(data) is not dict or key not in data or type(data[key]) is not str:
                return UNKNOWN
            for output, value in data.items():
                if (type(output) is not str or len(output) > 253
                        or not re.fullmatch(r"[-._a-zA-Z0-9]+", output)
                        or output == "." or output.startswith("..")):
                    return UNKNOWN
                # Raw workload outputs are not Helm writes; only the referenced YAML is.
                if output != key and (type(value) is not str or not re.fullmatch(
                        r"\{\{[ ]*\.[a-zA-Z_][a-zA-Z0-9_]*[ ]*\}\}", value)):
                    return UNKNOWN
            raw, marker = data[key], "__access_opaque_leaf__"
            if marker in raw:
                return UNKNOWN
            # Only a complete plain-key scalar may contain a quote placeholder.
            raw, replacements = re.subn(
                r"(?m)^([ ]*[a-zA-Z_][a-zA-Z0-9_.-]*:[ ]+)\{\{[ ]*\.[a-zA-Z_][a-zA-Z0-9_]*[ ]*\|[ ]*quote[ ]*\}\}[ ]*$",
                lambda match: match[1] + '"' + marker + '"', raw,
            )
            if "{{" in raw or "}}" in raw:
                return UNKNOWN
            seen = 0

            def opaque(value):
                nonlocal seen
                if type(value) is dict and value:
                    if any("{{" in k or "}}" in k or marker in k for k in value):
                        raise PlanError("unsupported producer key")
                    return {k: opaque(v) for k, v in value.items()}
                if type(value) is list or type(value) is dict:
                    raise PlanError("unsupported producer shape")
                if type(value) is str and marker in value:
                    if value != marker:
                        raise PlanError("producer placeholder must occupy a whole scalar")
                    seen += 1
                return UNKNOWN

            shape = opaque(document(raw))
            return shape if seen == replacements else UNKNOWN
        except (PlanError, RecursionError):
            return UNKNOWN

    def layers(self, release: dict, chart: str) -> list:
        root = self.roots["k8s-stack"]
        layers = []
        if chart in VENDOR_DEFAULTS:
            metadata = document(self.read(local(root, root, f"charts/{chart}/Chart.yaml"), "k8s-stack"))
            dependencies = [d for d in sequence(metadata.get("dependencies", [])) if type(d) is dict and d.get("name") == chart and d.get("alias", chart) == chart]
            if len(dependencies) != 1 or not re.fullmatch(r"[0-9A-Za-z.+_-]+", text(dependencies[0].get("version"))):
                raise PlanError("composition: unsupported fixed vendor dependency")
            archive = local(root, root, f"charts/{chart}/charts/{chart}-{dependencies[0]['version']}.tgz")
            try:
                with tarfile.open(fileobj=io.BytesIO(self.read_bytes(archive, "k8s-stack")), mode="r:gz") as package:
                    members = [m for m in package.getmembers() if m.name == chart + "/values.yaml"]
                    if len(members) != 1 or not members[0].isfile():
                        raise PlanError("composition: unsupported vendor defaults member")
                    layers.append({chart: document(package.extractfile(members[0]).read().decode("utf-8"))})
            except (tarfile.TarError, UnicodeError, OSError):
                raise PlanError("composition: cannot read fixed vendor defaults") from None
        layers.append(document(self.read(local(root, root, f"charts/{chart}/values.yaml"), "k8s-stack")))
        spec = release["spec"]
        namespace = release["metadata"].get("namespace", "default")
        for ref in sequence(spec.get("valuesFrom", [])):
            ref = mapping(ref, "kind name", "valuesKey optional targetPath")
            text(ref["name"])
            key = text(ref.get("valuesKey", "values.yaml"))
            if type(ref.get("optional", False)) is not bool:
                raise PlanError("composition: optional must be boolean")
            target = ref.get("targetPath", "")
            if "targetPath" in ref and (type(target) is not str or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*", target)):
                raise PlanError("composition: unsupported targetPath syntax")
            if ref["kind"] == "Secret":
                if target:
                    raise PlanError("composition: opaque Secret targetPath may assign arbitrary sibling values; unsupported")
                value = self.secret_shape(namespace, ref["name"], key)
            elif ref["kind"] == "ConfigMap":
                if target:
                    raise PlanError("composition: ConfigMap targetPath scalar parsing unsupported")
                cm = self.objects.get(("", "ConfigMap", namespace, ref["name"]))
                if cm is None:
                    if not ref.get("optional", False):
                        raise PlanError("composition: required namespace-local ConfigMap missing")
                    value = UNKNOWN
                else:
                    data = cm.get("data", {})
                    if type(data) is not dict or key not in data:
                        raise PlanError("composition: referenced ConfigMap valuesKey missing")
                    raw = data[key]
                    if type(raw) is not tuple and type(raw) is not str:
                        raise PlanError("composition: ConfigMap values must be text")
                    value = document(self.read(raw[1], raw[0]) if type(raw) is tuple else raw)
            else:
                raise PlanError("composition: unsupported valuesFrom kind")
            layers.append(value)
        if "values" in spec:
            if type(spec["values"]) is not dict:
                raise PlanError("composition: inline values must be a mapping")
            layers.append(spec["values"])
        return layers


def leaf(layers: list, path: str, expected: type, endpoint: str, default: object = MISSING):
    """Project ordered scalar overrides without erasing unknown siblings."""
    result = default
    for layer in layers:
        value = layer
        for part in path.split("."):
            if value is UNKNOWN:
                break
            if type(value) is not dict:
                value = INVALID
                break
            if part not in value:
                value = MISSING
                break
            value = value[part]
        if value is not MISSING:
            result = value
    if result is UNKNOWN:
        raise PlanError(f"{endpoint}: unresolved {path}; opaque Secret or optional ConfigMap override")
    if type(result) is not expected:
        raise PlanError(f"{endpoint}: missing or unsupported scalar {path}")
    return result
