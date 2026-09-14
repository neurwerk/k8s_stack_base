"""Real local Git plumbing against synthetic object stores, never status/diff filters."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import access_composition as composition
from scripts import access_git
from scripts import check_client_application_access as adapter

ROOT = Path(__file__).resolve().parents[2]


class GitSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def repo(self, files, root=None):
        """Write a tiny deterministic Git object graph; no Git write commands/hooks."""
        root = root or self.root
        gitdir = root / ".git"
        (gitdir / "objects").mkdir(parents=True)
        (gitdir / "refs/heads").mkdir(parents=True)
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
        (gitdir / "config").write_text("[core]\nrepositoryformatversion = 0\nbare = false\n")

        def obj(kind, payload):
            raw = kind + b" " + str(len(payload)).encode() + b"\0" + payload
            oid = hashlib.sha1(raw).hexdigest()
            path = gitdir / "objects" / oid[:2] / oid[2:]
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(zlib.compress(raw))
            return oid

        tree = {}
        for name, value in files.items():
            mode, data = value if isinstance(value, tuple) else ("100644", value)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if mode == "120000":
                path.symlink_to(os.fsdecode(data))
            else:
                path.write_bytes(data)
                path.chmod(0o755 if mode == "100755" else 0o644)
            node = tree
            for part in Path(name).parts[:-1]:
                node = node.setdefault(part, {})
            node[Path(name).name] = (mode, obj(b"blob", data))

        def store(node):
            entries = []
            for name, value in sorted(node.items(), key=lambda entry: entry[0] + ("/" if isinstance(entry[1], dict) else "")):
                mode, oid = ("40000", store(value)) if isinstance(value, dict) else value
                entries.append(mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(oid))
            return obj(b"tree", b"".join(entries))

        self.tree_sha = store(tree)
        self.sha = obj(b"commit", (f"tree {self.tree_sha}\nauthor Example <test@example.com> 1 +0000\n"
                                  "committer Example <test@example.com> 1 +0000\n\nfixture\n").encode())
        (gitdir / "refs/heads/main").write_text(self.sha + "\n")

    def test_attributes_clean_process_diff_and_fsmonitor_never_execute(self):
        self.repo({".gitattributes": b"*.yaml filter=trap diff=trap\n", "tracked.yaml": b"a: true\n"})
        marker = self.root / "must-not-exist"
        config = self.root / ".git/config"
        base = config.read_text()
        for field in ("clean", "process"):
            config.write_text(base + f'[filter "trap"]\n{field} = touch {marker}\nrequired = true\n'
                              f'[diff "trap"]\ntextconv = touch {marker}\n'
                              f'[core]\nfsmonitor = touch {marker}\n')
            snapshot = access_git.GitSnapshot(self.root)
            with self.subTest(field=field):
                self.assertFalse(snapshot.modified())
                self.assertEqual(snapshot.read(self.root / "tracked.yaml"), b"a: true\n")
                (self.root / "tracked.yaml").write_bytes(b"a: false\n")
                self.assertTrue(snapshot.modified([self.root / "tracked.yaml"]))
                self.assertFalse(marker.exists())
                (self.root / "tracked.yaml").write_bytes(b"a: true\n")

    def test_ignored_and_untracked_payloads_have_no_committed_provenance(self):
        self.repo({".gitignore": b"ignored/\n", "tracked.yaml": b"a: true\n"})
        ignored = self.root / "ignored/payload.yaml"
        ignored.parent.mkdir()
        ignored.write_bytes(b"a: false\n")
        snapshot = access_git.GitSnapshot(self.root)
        self.assertFalse(snapshot.modified())
        with self.assertRaisesRegex(composition.PlanError, "committed regular blob"):
            snapshot.read(ignored)
        extra = self.root / "extra.yaml"
        extra.write_bytes(b"a: true\n")
        self.assertTrue(snapshot.modified())
        with self.assertRaisesRegex(composition.PlanError, "committed regular blob"):
            snapshot.read(extra)

    def test_ignored_consumed_policy_and_lazy_values_report_modified(self):
        local_config = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization"}
        policy = {"access": {"boundary": "internet", "default": "internal"}, "endpoints": {"keycloak": {}}}
        defaults = {"authKeycloak": {"hostname": "login.example.com"}, "publicCertificates": {"useProduction": True},
                    "externalGateway": {"enabled": False}, "canonicalEndpointRouting": {"mode": "internal-traefik"}}
        release = {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
                   "metadata": {"name": "keycloak", "namespace": "apps"}, "spec": {
                       "chart": {"spec": {"chart": "./charts/keycloak/server", "sourceRef": {
                           "kind": "GitRepository", "name": "k8s-stack", "namespace": "flux-system"}}},
                       "valuesFrom": [{"kind": "ConfigMap", "name": "facts"}]}}
        source = {"apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "GitRepository",
                  "metadata": {"name": "k8s-stack", "namespace": "flux-system"},
                  "spec": {"url": adapter.BASE_URL, "ref": {"branch": "main"}}}
        stages = [{"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
                   "metadata": {"name": name, "namespace": "flux-system"}, "spec": {
                       "path": path, "sourceRef": {"kind": "GitRepository", "name": owner}}}
                  for name, path, owner in (("apps", "./releases/apps", "k8s-stack"), ("values", "./apps", "flux-system"))]
        for index, ignored in enumerate(("config/application-access.yaml", "config/values.yaml")):
            client, platform = self.root / f"client-{index}", self.root / f"platform-{index}"
            files = {".gitignore": (ignored + "\ncache/\n").encode(),
                     "clusters/prod-eu-1/kustomization.yaml": yaml.safe_dump({**local_config, "resources": ["selection.yaml"]}).encode(),
                     "clusters/prod-eu-1/selection.yaml": yaml.safe_dump_all([source, *stages]).encode(),
                     "apps/kustomization.yaml": yaml.safe_dump({**local_config, "namespace": "apps",
                         "generatorOptions": {"disableNameSuffixHash": True}, "configMapGenerator": [
                             {"name": "facts", "files": ["values.yaml=../config/values.yaml"]}]}).encode(),
                     "config/application-access.yaml": yaml.safe_dump(policy).encode(),
                     "config/values.yaml": yaml.safe_dump(defaults).encode()}
            payload = files.pop(ignored)
            self.repo(files, client)
            (client / ignored).write_bytes(payload)
            self.repo({"charts/keycloak/server/values.yaml": yaml.safe_dump(defaults).encode(),
                       "releases/apps/kustomization.yaml": yaml.safe_dump({**local_config, "resources": ["release.yaml"]}).encode(),
                       "releases/apps/release.yaml": yaml.safe_dump(release).encode()}, platform)
            cache = client / "cache/unrelated.bin"
            cache.parent.mkdir()
            cache.write_bytes(b"not an input")
            snapshot = access_git.GitSnapshot(client)
            self.assertFalse(snapshot.modified())
            read_bytes = Path.read_bytes
            with self.subTest(ignored=ignored), patch.object(Path, "read_bytes", autospec=True,
                    side_effect=lambda path: self.fail("ignored cache was read") if path == cache else read_bytes(path)):
                plan, identities = adapter.derive_plan(client, platform)
            self.assertEqual(set(plan["endpoints"]), {"keycloak"})
            self.assertEqual(identities["client"], snapshot.sha)
            self.assertEqual(identities["clientSnapshot"], "modified")
            self.assertTrue(snapshot.modified([client / ignored]))

    def test_composition_cannot_read_an_ignored_platform_resource(self):
        self.repo({".gitignore": b"ignored/\n",
                   "releases/apps/kustomization.yaml": b"apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [../../ignored/payload.yaml]\n"})
        ignored = self.root / "ignored/payload.yaml"
        ignored.parent.mkdir()
        ignored.write_bytes(b"apiVersion: v1\nkind: Namespace\nmetadata: {name: hidden}\n")
        client = self.root / "client"
        cluster = client / "clusters/prod-eu-1"
        cluster.mkdir(parents=True)
        (cluster / "kustomization.yaml").write_text("apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [selection.yaml]\n")
        (cluster / "selection.yaml").write_text("apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\nmetadata: {name: apps, namespace: flux-system}\nspec:\n  path: ./releases/apps\n  sourceRef: {kind: GitRepository, name: k8s-stack}\n")
        with self.assertRaisesRegex(composition.PlanError, "committed regular blob"):
            composition.Composition(client, self.root, "prod-eu-1", access_git.GitSnapshot(self.root))

    def test_changed_bytes_modes_and_symlink_payloads_are_rejected(self):
        self.repo({"tracked.yaml": b"a: true\n", "alias.yaml": ("120000", b"tracked.yaml")})
        snapshot = access_git.GitSnapshot(self.root)
        self.assertFalse(snapshot.modified())
        with self.assertRaisesRegex(composition.PlanError, "committed regular blob"):
            snapshot.read(self.root / "alias.yaml")
        with self.assertRaisesRegex(composition.PlanError, "symlink payloads"):
            composition.local(self.root, self.root, "alias.yaml")
        path = self.root / "tracked.yaml"
        path.write_bytes(b"a: false\n")
        with self.assertRaisesRegex(composition.PlanError, "bytes differ"):
            snapshot.read(path)
        path.write_bytes(b"a: true\n")
        path.chmod(0o755)
        with self.assertRaisesRegex(composition.PlanError, "bytes differ"):
            snapshot.read(path)

    def test_missing_promised_tree_never_fetches_or_runs_remote_helper(self):
        self.repo({"tracked.yaml": b"a: true\n"})
        marker = self.root / "must-not-exist"
        config = self.root / ".git/config"
        config.write_text(config.read_text().replace("repositoryformatversion = 0", "repositoryformatversion = 1") + f'[extensions]\npartialClone = origin\n[remote "origin"]\npromisor = true\nurl = ext::touch {marker}\n[protocol "ext"]\nallow = always\n')
        (self.root / ".git/objects" / self.tree_sha[:2] / self.tree_sha[2:]).unlink()
        before = {p: p.read_bytes() for p in (self.root / ".git").rglob("*") if p.is_file()}
        with self.assertRaisesRegex(composition.PlanError, "fetching is prohibited"):
            access_git.GitSnapshot(self.root)
        self.assertFalse(marker.exists())
        self.assertEqual(before, {p: p.read_bytes() for p in (self.root / ".git").rglob("*") if p.is_file()})

    def test_git_environment_and_command_set_are_bounded(self):
        self.repo({"tracked.yaml": b"a: true\n"})
        run = access_git.subprocess.run
        with patch.dict(os.environ, {"GIT_DIR": "unrelated", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "unsafe"}), patch.object(access_git.subprocess, "run", wraps=run) as calls:
            snapshot = access_git.GitSnapshot(self.root)
            self.assertFalse(snapshot.modified())
        for call in calls.call_args_list:
            args, env = call.args[0], call.kwargs["env"]
            self.assertIn("--no-lazy-fetch", args)
            self.assertIn("--no-replace-objects", args)
            self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "")
            self.assertNotIn("GIT_DIR", env)
            self.assertNotIn("GIT_CONFIG_COUNT", env)
            self.assertFalse(set(args) & {"status", "diff", "hash-object", "fetch"})
        with self.assertRaisesRegex(composition.PlanError, "unsupported Git operation"):
            access_git.git(self.root, "status")


if __name__ == "__main__":
    unittest.main()
