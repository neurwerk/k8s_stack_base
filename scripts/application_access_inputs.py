"""Read the selected platform checkout ref; never fetch or read a checker pin."""

import argparse
import re
from pathlib import Path

import yaml


class SourceLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[str, object]:
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("platform source requires mappings")
        mapping: dict[str, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if type(key) is not str or key in mapping:
                raise ValueError("platform source requires unique string keys")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def platform_ref(content: str) -> str:
    """Return main, an exact stable tag ref, or a full frozen-alpha commit."""
    try:
        for token in yaml.scan(content):
            if isinstance(
                token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)
            ):
                raise ValueError("platform source must not use YAML aliases or tags")
        source = yaml.load(content, Loader=SourceLoader)
        if (
            type(source) is not dict
            or type(source.get("metadata")) is not dict
            or type(source.get("spec")) is not dict
            or type(source["spec"].get("ref")) is not dict
        ):
            raise ValueError("platform source requires mappings")
        if (
            source["apiVersion"] != "source.toolkit.fluxcd.io/v1"
            or source["kind"] != "GitRepository"
            or source["metadata"]["name"] != "k8s-stack"
            or source["metadata"]["namespace"] != "flux-system"
            or source["spec"]["url"] != "https://github.com/neurwerk/k8s_stack_base.git"
        ):
            raise ValueError("unexpected platform source identity")
        ref = source["spec"]["ref"]
        if ref == {"branch": "main"}:
            return "refs/heads/main"
        if (
            set(ref) == {"tag"}
            and type(ref["tag"]) is str
            and re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", ref["tag"])
        ):
            return f"refs/tags/{ref['tag']}"
        if (
            set(ref) == {"commit"}
            and type(ref["commit"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", ref["commit"])
        ):
            return ref["commit"]
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError, yaml.YAMLError):
        raise ValueError("invalid platform source YAML; input details withheld") from None
    raise ValueError(
        "platform source must select only main, an exact stable tag, or a full alpha commit"
    )


def main(argv: list[str] | None = None) -> None:
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, "Access planning inputs: invalid CLI arguments; input details withheld.\n")

    parser = Parser(description=__doc__)
    parser.add_argument("--client-root", type=Path, required=True)
    parser.add_argument("--field", choices=("platform_ref",), required=True)
    args = parser.parse_args(argv)
    try:
        value = platform_ref(
            (args.client_root / "clusters/prod-eu-1/platform-source.yaml").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, yaml.YAMLError):
        parser.exit(
            1,
            "Access planning inputs: cannot read or validate platform source; input details withheld.\n",
        )
    print(value)


if __name__ == "__main__":
    main()
