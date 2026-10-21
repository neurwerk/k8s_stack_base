# Base documentation

Base owns shared Helm charts, Flux release packages, platform defaults, and release validation. Component repositories own application code and images; Tooling owns setup and operational commands. Add-ons own their product packages, while clients select releases, supply environment settings, and define deployment order.

- [Architecture](architecture/README.md): package composition, configuration ownership, and service relationships.
- [Development](development/README.md): local tools, validation, and release metadata.
- [Operations](operations/README.md): configuration, adoption, and recovery boundaries.
- [AI endpoints](endpoints.md): API and inspected-proxy client configuration.

Use the selected release's [manifest](../release/manifest.yaml) for exact packages, prerequisites, images, and compatibility. A package's presence in the repository does not establish that it can be selected.
