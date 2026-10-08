"""Select only the rendered setup Job for an independently authorized rerun."""

import argparse
import copy
import re
import sys

import yaml


def rerun_job(documents, release, namespace, name):
    if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", name) or name == release + "-setup":
        raise ValueError("Use a fresh DNS-label Job name, distinct from the Helm hook")
    jobs = [doc for doc in documents if doc and doc.get("kind") == "Job"
            and doc.get("metadata", {}).get("name") == release + "-setup"]
    if len(jobs) != 1:
        raise ValueError("Expected exactly one rendered ContextForge setup Job")
    job = copy.deepcopy(jobs[0])
    template = job["spec"]["template"]
    if (template["metadata"]["labels"].get("app.kubernetes.io/component") != "setup"
            or template["spec"]["containers"][0].get("command") != ["python3", "-B", "/setup/setup.py"]):
        raise ValueError("Refusing a Job that is not the supported setup runner")
    job["metadata"] = {"name": name, "namespace": namespace,
                       "labels": job["metadata"].get("labels", {})}
    # Rendered chart input has no live controller identity; discard any supplied one.
    job["spec"].pop("selector", None)
    job["spec"].pop("manualSelector", None)
    template["metadata"] = {"labels": {key: value for key, value in template["metadata"]["labels"].items()
                                     if key.startswith("app.kubernetes.io/")}}
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default="contextforge")
    parser.add_argument("--namespace", default="contextforge")
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        job = rerun_job(yaml.safe_load_all(sys.stdin), args.release, args.namespace, args.name)
    except (ValueError, KeyError, TypeError, yaml.YAMLError):
        parser.error("Invalid setup render or fresh Job name; no manifest produced")
    yaml.safe_dump(job, sys.stdout, sort_keys=False)


if __name__ == "__main__":
    main()
