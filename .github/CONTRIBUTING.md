# Contributing

Thank you for helping improve the base platform repository. This repository owns
Helm charts, platform release contracts, namespaces, platform defaults, and
platform validation. Service behavior belongs in the relevant service repository,
and client-specific facts or sizing belong in the relevant client repository.

## Issues

Before opening an issue:

1. Search existing issues and avoid duplicates.
2. Use one issue for one problem or outcome.
3. Write a descriptive title that states the behavior or desired result.
4. Select the appropriate issue form. Maintainers use native `Bug`, `Feature`,
   and `Task` issue types.
5. Describe the problem before proposing an implementation.
6. For bugs, include reproduction steps, expected and actual behavior, the
   affected platform version or commit, environment details, and useful evidence.
7. For features, explain the user or platform problem, desired outcome, and why
   current behavior is insufficient.
8. Define objective acceptance criteria.
9. State scope and non-goals when boundaries could be ambiguous.
10. Link related issues, pull requests, designs, and documentation.
11. Remove secrets, credentials, private keys, tokens, and client data from all
    issue content and attachments.

Do not report security vulnerabilities in a public issue. Follow the
[security policy](SECURITY.md) and use private vulnerability reporting.

Use [GitHub Discussions](https://github.com/neurwerk/k8s_stack_base/discussions)
for questions, support, and general conversation.

Reporters do not assign priority. During triage, maintainers validate the issue,
confirm its scope and type, request missing information, and assign priority,
area, milestone or project, and owner where appropriate. Maintainers may close
duplicates, incomplete reports, support questions, and out-of-scope requests.

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
