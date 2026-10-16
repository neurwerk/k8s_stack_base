"""Constrain the scheduled observer's Kubernetes and private-network authority."""

import unittest

from helm import documents, render, resource
from test_agentgateway_inspection import binding_values


class GatewayObservationTests(unittest.TestCase):
    def test_bounded_job_can_only_publish_named_status_and_read_runtime_identity(self):
        values = binding_values()
        values["aiGateway"]["proxy"]["port"] = 8443
        values["infraAgentgatewayWrapper"] = {"observation": {
            "enabled": True,
            "image": "example.test/tooling:review-only",
            "kubernetesApiEgress": [{"cidr": "192.0.2.1/32", "port": 6443}],
        }}
        rendered = render("agentgateway", values, namespace="infra-agentgateway")
        job = resource(rendered, "CronJob", "agentgateway-observation")["spec"]
        self.assertEqual(job["concurrencyPolicy"], "Forbid")
        self.assertLessEqual(job["jobTemplate"]["spec"]["activeDeadlineSeconds"], 90)
        pod = job["jobTemplate"]["spec"]["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"], "agentgateway-observation")
        self.assertTrue(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(any("secret" in volume for volume in pod.get("volumes", [])))
        roles = [doc for doc in documents(rendered) if doc["kind"] == "Role"
                 and doc["metadata"]["name"] == "agentgateway-observation"]
        self.assertEqual(len(roles), 2)
        for role in roles:
            for rule in role["rules"]:
                self.assertFalse(set(rule["resources"]) & {"secrets", "pods/exec", "deployments"})
                self.assertFalse(set(rule["verbs"]) & {"create", "delete", "update", "watch", "*"})
                if "patch" in rule["verbs"]:
                    self.assertEqual(role["metadata"]["namespace"], "frontend-studio")
                    self.assertEqual(rule["resources"], ["configmaps"])
                    self.assertEqual(rule["resourceNames"], ["agentgateway-status-config-map"])
        boundary = resource(rendered, "NetworkPolicy", "agentgateway-observation")["spec"]
        self.assertEqual(boundary["ingress"], [])
        ports = {entry["port"] for rule in boundary["egress"] for entry in rule["ports"]}
        self.assertEqual(ports, {53, 15000, 15021, values["aiGateway"]["proxy"]["port"], 6443})
        self.assertFalse(any(peer.get("ipBlock", {}).get("cidr") == "0.0.0.0/0"
                             for rule in boundary["egress"] for peer in rule["to"]))
        gateway = resource(rendered, "NetworkPolicy", "infra-agentgateway-data-plane-network-policy")["spec"]
        peers = [rule for rule in gateway["ingress"] if {"podSelector": {"matchLabels": {
            "app.kubernetes.io/name": "agentgateway-observation"}}} in rule["from"]]
        self.assertEqual(len(peers), 1)
        self.assertEqual({port["port"] for port in peers[0]["ports"]}, ports - {53, 6443})


if __name__ == "__main__":
    unittest.main()
