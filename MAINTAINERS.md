# Maintainers

This document describes the maintainer team, responsibilities, review procedures, and project governance for yaatv.

## Active Maintainers

| Maintainer | GitHub | Role | Area |
| --- | --- | --- | --- |
| cavyion | [@cavyion](https://github.com/cavyion) | Lead Maintainer | Core CLI, architecture, releases, security |

## Responsibilities

Maintainers are responsible for:

1. **Reviewing Pull Requests**: Evaluating community contributions for technical correctness, test coverage, code readability, and alignment with yaatv's design principles (standard-library first, minimal dependencies).
2. **Merge Authority & Attribution**: Enforcing the [Contributor Attribution and Merge Policy](CONTRIBUTING.md#contributor-attribution-and-merge-policy). Merges use **Squash and Merge**, ensuring the original contributor retains commit authorship and co-author trailers (`Co-authored-by:`) are included for multi-person contributions.
3. **Release Management**: Tagging official releases (`v*`), verifying multi-platform binary builds (Windows, Linux, macOS), and submitting Windows assets to Microsoft Security Intelligence.
4. **Security Triage**: Triaging private vulnerability reports submitted through GitHub Security Advisories as outlined in [SECURITY.md](SECURITY.md).
5. **Project Direction**: Defining CLI scope, stability guarantees, and long-term maintenance.

## Becoming a Maintainer

Contributors who consistently submit high-quality patches, thorough reviews, or assist with issue triage and platform verification across Windows, Linux, and macOS may be invited to join as maintainers.

