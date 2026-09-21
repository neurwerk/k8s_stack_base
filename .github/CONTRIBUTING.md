# Contributing

Thank you for helping improve the base platform repository. This repository owns
Helm charts, platform release contracts, namespaces, platform defaults, and
platform validation. Service behavior belongs in the relevant service repository,
and client-specific facts or sizing belong in the relevant client repository.

## Issues

Use a short form or open a blank issue. A clear title and a few useful sentences
are enough. Keep each issue focused on one problem or outcome and check for an
existing issue first.

For bugs, describe what happened and what you expected. Add reproduction steps,
versions, or evidence if available. For features and tasks, explain what should
change and why. Leave unknown details open rather than inventing answers or
filling sections with boilerplate. Maintainers can ask for details during triage.

Remove secrets and private data from issue content and attachments.

Do not report security vulnerabilities in a public issue. Follow the
[security policy](SECURITY.md) and use private vulnerability reporting.

Use [GitHub Discussions](https://github.com/neurwerk/k8s_stack_base/discussions)
for questions, support, and general conversation.

Maintainers assign issue types (`Bug`, `Feature`, or `Task`), priority, and other
classification during triage as needed.

## Pull Requests

Keep pull requests focused and link the issue they address. Update tests and
documentation with behavior changes, follow the repository conventions, and run
`make check` before requesting review. Never commit credentials, private keys,
provider tokens, recovery material, real Secret manifests, or generated caches.

Apply the generated-note category label that describes the change. Use
`skip-changelog` or `release: none` only when the pull request must be excluded
from generated notes. Reserve `release: platform` for platform release
preparation; missing label configuration never bypasses release evidence or
validation.
