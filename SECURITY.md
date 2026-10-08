# Security

Report a vulnerability privately: on this repository's **Security** tab,
choose **Report a vulnerability**. Please do not open a public issue for a
security problem.

A useful report names the service (hookrelay, hookjudge or hookprobe), the
version or commit, and what an attacker gains. A boundary in
[docs/containment.md](docs/containment.md) that does not hold is exactly the
report this project wants: each row there says what it stops and what it does
not, and a test is supposed to fail when it breaks.

Supported: the latest release and `main`.
