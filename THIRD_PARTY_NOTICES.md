# Third-Party Notices

The repository's [MIT license](LICENSE) applies only to content owned by
Neurwerk contributors. It does not relicense Helm dependencies under
`charts/**/charts/*.tgz`. Those archives retain their upstream licenses,
copyrights, notices, and trademarks.

This inventory was derived from every `Chart.yaml` inside the committed
archives and from license files at the exact upstream release tags named below.
The full applicable texts are available offline under [`LICENSES/`](LICENSES/).
`Chart.lock` remains authoritative for direct dependency versions and lock
digests; `release/manifest.yaml` records each archive's SHA-256 digest.

## Offline Texts

| Local file | Authoritative source |
| --- | --- |
| [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt) | Exact Apache-2.0 text from [cert-manager `v1.20.2`](https://github.com/cert-manager/cert-manager/blob/v1.20.2/LICENSE); the mapped upstream chart metadata or repository root declares Apache-2.0 |
| [`LICENSES/Langfuse-MIT.txt`](LICENSES/Langfuse-MIT.txt) | Exact [Langfuse Kubernetes `langfuse-1.5.34` license](https://github.com/langfuse/langfuse-k8s/blob/langfuse-1.5.34/LICENSE) |
| [`LICENSES/OpenBao-MPL-2.0.txt`](LICENSES/OpenBao-MPL-2.0.txt) | Exact [OpenBao Helm `openbao-0.29.1` license](https://github.com/openbao/openbao-helm/blob/openbao-0.29.1/LICENSE) |
| [`LICENSES/AgentGateway-ATTRIBUTION.txt`](LICENSES/AgentGateway-ATTRIBUTION.txt) | Copyright attribution from [AgentGateway `v1.5.0`](https://github.com/agentgateway/agentgateway/blob/v1.5.0/LICENSE) |
| [`LICENSES/Bitnami-Charts-2024-ATTRIBUTION.txt`](LICENSES/Bitnami-Charts-2024-ATTRIBUTION.txt) | Exact attribution preamble from [Bitnami Charts at `minio/14.10.5`](https://github.com/bitnami/charts/blob/minio/14.10.5/LICENSE.md), used by the mapped 2024 chart tags |
| [`LICENSES/Bitnami-Charts-2025-ATTRIBUTION.txt`](LICENSES/Bitnami-Charts-2025-ATTRIBUTION.txt) | Exact attribution preamble from [Bitnami Charts at `clickhouse/8.0.5`](https://github.com/bitnami/charts/blob/clickhouse/8.0.5/LICENSE.md), used by the mapped 2025 chart tags |
| [`LICENSES/Grafana-Helm-Charts-ATTRIBUTION.txt`](LICENSES/Grafana-Helm-Charts-ATTRIBUTION.txt) | Copyright attribution from [Grafana Helm Charts `grafana-9.0.0`](https://github.com/grafana/helm-charts/blob/grafana-9.0.0/LICENSE) |
| [`LICENSES/OpenSearch-Helm-Charts-NOTICE.txt`](LICENSES/OpenSearch-Helm-Charts-NOTICE.txt) | Exact [OpenSearch Helm Charts `opensearch-2.27.0` NOTICE](https://github.com/opensearch-project/helm-charts/blob/opensearch-2.27.0/NOTICE.txt) |

## Direct Archives

| Vendored archive | Exact upstream source | Offline license and notice |
| --- | --- | --- |
| `charts/agentgateway/charts/agentgateway-1.5.0.tgz` | [agentgateway `v1.5.0`](https://github.com/agentgateway/agentgateway/tree/v1.5.0) | Apache-2.0; AgentGateway attribution |
| `charts/cert-manager/approver-policy/charts/cert-manager-approver-policy-v0.25.1.tgz` | [approver-policy `v0.25.1`](https://github.com/cert-manager/approver-policy/tree/v0.25.1) | Apache-2.0 |
| `charts/cert-manager/controller/charts/cert-manager-v1.20.2.tgz` | [cert-manager `v1.20.2`](https://github.com/cert-manager/cert-manager/tree/v1.20.2) | Apache-2.0 |
| `charts/external-secrets/charts/external-secrets-2.9.0.tgz` | [external-secrets chart `2.9.0` at source tag `v2.10.0`](https://github.com/external-secrets/external-secrets/tree/v2.10.0/deploy/charts/external-secrets) | Apache-2.0 |
| `charts/fluent-bit/charts/fluent-bit-0.48.9.tgz` | [Fluent Helm Charts `fluent-bit-0.48.9`](https://github.com/fluent/helm-charts/tree/fluent-bit-0.48.9/charts/fluent-bit) | Apache-2.0 |
| `charts/kube-prometheus-stack/charts/kube-prometheus-stack-72.4.0.tgz` | [Prometheus Community Helm Charts `kube-prometheus-stack-72.4.0`](https://github.com/prometheus-community/helm-charts/tree/kube-prometheus-stack-72.4.0/charts/kube-prometheus-stack) | Apache-2.0 |
| `charts/langfuse/charts/langfuse-1.5.34.tgz` | [Langfuse Kubernetes `langfuse-1.5.34`](https://github.com/langfuse/langfuse-k8s/tree/langfuse-1.5.34/charts/langfuse) | Langfuse MIT |
| `charts/openbao/charts/openbao-0.29.1.tgz` | [OpenBao Helm `openbao-0.29.1`](https://github.com/openbao/openbao-helm/tree/openbao-0.29.1/charts/openbao) | OpenBao MPL-2.0 |
| `charts/opensearch/charts/opensearch-2.27.0.tgz` | [OpenSearch Helm Charts `opensearch-2.27.0`](https://github.com/opensearch-project/helm-charts/tree/opensearch-2.27.0/charts/opensearch) | Apache-2.0; OpenSearch NOTICE |
| `charts/reloader/charts/reloader-2.2.16.tgz` | [Reloader `v1.4.21`](https://github.com/stakater/Reloader/tree/v1.4.21/deployments/kubernetes/chart/reloader) | Apache-2.0 |
| `charts/trust-manager/charts/trust-manager-v0.24.0.tgz` | [trust-manager `v0.24.0`](https://github.com/cert-manager/trust-manager/tree/v0.24.0/deploy/charts/trust-manager) | Apache-2.0 |

## Nested Charts

The following components are physically embedded in a direct archive. Paths are
archive-internal paths, not additional repository files.

| Parent archive | Embedded component | Exact source | Offline license and notice |
| --- | --- | --- | --- |
| External Secrets 2.9.0 | `external-secrets/charts/bitwarden-sdk-server`, `v0.6.0` | Declared OCI dependency in the [exact chart source](https://github.com/external-secrets/external-secrets/blob/v2.10.0/deploy/charts/external-secrets/Chart.yaml) | Apache-2.0 under the External Secrets repository license |
| kube-prometheus-stack 72.4.0 | `kube-prometheus-stack/charts/crds`, `0.0.0` | Included in [kube-prometheus-stack `72.4.0`](https://github.com/prometheus-community/helm-charts/tree/kube-prometheus-stack-72.4.0/charts/kube-prometheus-stack) | Apache-2.0 |
| kube-prometheus-stack 72.4.0 | `kube-prometheus-stack/charts/grafana`, `9.0.0` | [Grafana Helm Charts `grafana-9.0.0`](https://github.com/grafana/helm-charts/tree/grafana-9.0.0/charts/grafana) | Apache-2.0; Grafana Helm Charts attribution |
| kube-prometheus-stack 72.4.0 | `kube-prometheus-stack/charts/kube-state-metrics`, `5.33.1` | [Prometheus Community Helm Charts `kube-state-metrics-5.33.1`](https://github.com/prometheus-community/helm-charts/tree/kube-state-metrics-5.33.1/charts/kube-state-metrics) | Apache-2.0 |
| kube-prometheus-stack 72.4.0 | `kube-prometheus-stack/charts/prometheus-node-exporter`, `4.46.0` | [Prometheus Community Helm Charts `prometheus-node-exporter-4.46.0`](https://github.com/prometheus-community/helm-charts/tree/prometheus-node-exporter-4.46.0/charts/prometheus-node-exporter) | Apache-2.0 |
| kube-prometheus-stack 72.4.0 | `kube-prometheus-stack/charts/prometheus-windows-exporter`, `0.10.0` | [Prometheus Community Helm Charts `prometheus-windows-exporter-0.10.0`](https://github.com/prometheus-community/helm-charts/tree/prometheus-windows-exporter-0.10.0/charts/prometheus-windows-exporter) | Apache-2.0 |
| Langfuse 1.5.34 | `langfuse/charts/clickhouse`, `8.0.5` | [Bitnami Charts `clickhouse/8.0.5`](https://github.com/bitnami/charts/tree/clickhouse/8.0.5/bitnami/clickhouse) | Apache-2.0; Bitnami 2025 attribution |
| Langfuse 1.5.34 | `langfuse/charts/clickhouse/charts/zookeeper`, `13.7.4` | [Bitnami Charts `zookeeper/13.7.4`](https://github.com/bitnami/charts/tree/zookeeper/13.7.4/bitnami/zookeeper) | Apache-2.0; Bitnami 2025 attribution |
| Langfuse 1.5.34 | `langfuse/charts/clickhouse/charts/zookeeper/charts/common`, `2.30.0` | [Bitnami Charts `common/2.30.0`](https://github.com/bitnami/charts/tree/common/2.30.0/bitnami/common) | Apache-2.0; Bitnami 2025 attribution |
| Langfuse 1.5.34 | `langfuse/charts/clickhouse/charts/common`, `2.30.0` | [Bitnami Charts `common/2.30.0`](https://github.com/bitnami/charts/tree/common/2.30.0/bitnami/common) | Apache-2.0; Bitnami 2025 attribution |
| Langfuse 1.5.34 | `langfuse/charts/common`, `2.30.0` | [Bitnami Charts `common/2.30.0`](https://github.com/bitnami/charts/tree/common/2.30.0/bitnami/common) | Apache-2.0; Bitnami 2025 attribution |
| Langfuse 1.5.34 | `langfuse/charts/minio`, `14.10.5` | [Bitnami Charts `minio/14.10.5`](https://github.com/bitnami/charts/tree/minio/14.10.5/bitnami/minio) | Apache-2.0; Bitnami 2024 attribution |
| Langfuse 1.5.34 | `langfuse/charts/minio/charts/common`, `2.29.0` | [Bitnami Charts `common/2.29.0`](https://github.com/bitnami/charts/tree/common/2.29.0/bitnami/common) | Apache-2.0; Bitnami 2024 attribution |
| Langfuse 1.5.34 | `langfuse/charts/postgresql`, `16.4.9` | [Bitnami Charts `postgresql/16.4.9`](https://github.com/bitnami/charts/tree/postgresql/16.4.9/bitnami/postgresql) | Apache-2.0; Bitnami 2025 attribution |
| Langfuse 1.5.34 | `langfuse/charts/postgresql/charts/common`, `2.29.1` | [Bitnami Charts `common/2.29.1`](https://github.com/bitnami/charts/tree/common/2.29.1/bitnami/common) | Apache-2.0; Bitnami 2024 attribution |
| Langfuse 1.5.34 | `langfuse/charts/valkey`, `2.2.4` | [Bitnami Charts `valkey/2.2.4`](https://github.com/bitnami/charts/tree/valkey/2.2.4/bitnami/valkey) | Apache-2.0; Bitnami 2025 attribution |
| Langfuse 1.5.34 | `langfuse/charts/valkey/charts/common`, `2.29.1` | [Bitnami Charts `common/2.29.1`](https://github.com/bitnami/charts/tree/common/2.29.1/bitnami/common) | Apache-2.0; Bitnami 2024 attribution |

The archives contain chart source and rendered configuration inputs, not the
referenced container images or application binaries. Runtime images and
applications have separate licensing. Consult exact image references in
`release/manifest.yaml` before redistributing runtime artifacts.
