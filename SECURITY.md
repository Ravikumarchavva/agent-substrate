# Security policy

## Reporting a vulnerability

Please do not open a public issue or pull request for a vulnerability.

Use GitHub private vulnerability reporting: open the repository's **Security** tab, choose **Report a vulnerability**
(a private security advisory). Include the affected package and version, a description, reproduction steps and the
impact you see. Do not include real credentials or customer data.

## Response targets

| Stage | Target |
| --- | --- |
| Acknowledge the report | 3 business days |
| Initial assessment and severity | 7 days |
| Fix or mitigation for critical/high | 30 days |
| Fix for medium/low | next regular release |

We will credit reporters in the advisory unless you ask us not to.

## Supported versions

Only the latest released minor version of each package receives security fixes. Packages are pre-1.0, so older
minors are not backported.

| Package | Supported |
| --- | --- |
| agent-substrate | latest release |
| substrate-data-client | latest release |
| substrate-data, substrate-cloud, substrate-ui, substrate-data-web | latest release (container images) |

## Scope

In scope: code in this repository and the published packages and images. Out of scope: vulnerabilities in third-party
dependencies with no exploitable path through this code (report those upstream), and issues requiring a
pre-compromised host.
