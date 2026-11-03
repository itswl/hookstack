---
title: A read-only identity still reads secret values, so posture says how much and the guard refuses the obvious prints
status: implemented
date: 2026-11-03
scope: hookprobe
---

## Decision

Posture asks two reading questions beside the mutating ones and records the
answers as `reads_secrets`, without letting them touch the verdict. For a
cluster: `kubectl auth can-i get|list secrets -A`, and any `secrets` or
wildcard rule in `can-i --list`. For AWS: a second simulation of
`secretsmanager:GetSecretValue`, `ssm:GetParameter`, `kms:Decrypt` and
`s3:GetObject`. Their failures go to `read_errors`, never to the errors that
make a verdict unverifiable.

The bash guard refuses printing a secret's value under every posture, before
the posture branch. That covers a Secret's data from kubectl (`get` with any
output but the table forms, `--raw` on a secrets path, `view-secret`,
`oc extract`), Secrets Manager values, SSM parameters with decryption, KMS
decryption and plaintext data keys, `sts get-session-token` and
`get-federation-token`, and `aws configure` printing the runner's own keys.
Whether a secret exists, which keys it holds and when it changed stays open.

## Why

A second deployment running copies of these files measured its read-only
identities. Its cluster identity read Secret objects in every cluster, and its
read-only AWS policy read S3 object contents. Neither shows up in a posture
that only asks what an identity may change, and both are reads a read-only
investigator is allowed to make.

A value read into an investigation is in the model's context, sent to the
provider, kept in the transcript, and one summary away from a chat card. The
output guard records that a credential came back, after the fact. Refusing the
few commands whose whole output is a secret costs an investigation almost
nothing: it asks whether the secret is there and when it changed, not what it
says.

Reported rather than refused at startup, because a read-only identity is
allowed to read, and an upgrade must not stop a node that ran yesterday.

## Consequences

- `/v1/posture` shows how much a read-only node can read that is a secret, so
  an operator can scope the identity. The verdict means what it meant.
- `kubectl get secret x -o yaml` and `aws secretsmanager get-secret-value` are
  refused, with a sentence naming what to ask instead. This overturns the
  2026-09-09 note that left them allowed on purpose; that note is corrected in
  place.
- Not caught: a raw API URL built at runtime, a pod that mounts the secret,
  another vendor's CLI, or a secret in a log line or a config file. The
  credential is what holds, and the output guard still records what comes
  back.
- An identity that may not simulate the reading actions keeps its verdict, and
  shows the failure in `read_errors`.
