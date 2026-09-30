# Security Policy

## Reporting a vulnerability

Please do not report security vulnerabilities in public issues, pull
requests, or discussions.

Report them privately through GitHub Private Vulnerability Reporting: open the
repository's **Security** tab and choose **Report a vulnerability**. This
creates a private security advisory visible only to you and the maintainers.

Please include:

- a description of the issue and its impact
- the RepoCheck version and Python version
- steps or a minimal repository to reproduce it

## Supported versions

Security fixes are made against the latest release.

## Scope

RepoCheck analyzes local files and local Git state. It runs Git without a
shell and never fetches or contacts remotes. Reports about untrusted
repository content causing RepoCheck to run commands, access files outside
the analyzed path, hang, or report misleading results are in scope.
