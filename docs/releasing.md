# Releasing

A release is one tag and four images. There is no package to publish and no
registry but the container one.

## What a release is

| artefact | where | named by |
| --- | --- | --- |
| `hookrelay`, `hookjudge`, `hookprobe`, `lark-bridge` images | `ghcr.io/itswl/<service>` | `<semver>` tag |
| the quickstart | `docker-compose.quickstart.yml` | the same tags, **digest-pinned** |
| the notes | `.agents/notes/implemented/` | the git log between two tags |

The release notes are not a separate document. They are the implemented decision
records that landed since the last tag — that is what the bucket is for, and a
second place to write them is the duplicate-source mistake this repository keeps
finding its way back to. There are **no GitHub Release objects**: the annotated
tag's own message is the note, which is how 0.5.0, 0.6.0 and 0.7.0 shipped.

## The steps

1. **The gate, read, not chained.** `bash scripts/gate.sh` as its own step; read
   the verdict. Never `gate.sh && docker push` — an exit code is not a report
   (`AGENTS.md` has the incident that taught this).
2. **Tag.** `git tag -a vX.Y.Z -m "…" && git push origin vX.Y.Z`. The release
   workflow builds and pushes the four images at that tag. When the release
   changes a schema, the message ends with what a rollback does with the rows it
   wrote (see Rollback).
3. **Pin the quickstart to the digest, not the tag alone.** A tag can be moved; a
   digest cannot. For each image:

   ```bash
   tok=$(curl -fsS "https://ghcr.io/token?scope=repository:itswl/hookrelay:pull&service=ghcr.io" \
         | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
   curl -fsSI -H "Authorization: Bearer $tok" \
     -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json" \
     "https://ghcr.io/v2/itswl/hookrelay/manifests/X.Y.Z" | tr -d '\r' | grep -i docker-content-digest
   ```

   Write the result as `ghcr.io/itswl/hookrelay:X.Y.Z@sha256:…` in
   `docker-compose.quickstart.yml`. This is why the file cannot point at an older
   tag: it needs whichever release carries everything the demo shows.
4. **Re-run the quickstart the way a stranger does** — `curl` the published
   compose, `docker compose up`, `bash scripts/demo.sh`. The file's own comment
   records three releases that were wrong until somebody actually ran it.
   **From an empty directory, not the checkout.** Compose reads `./.env` for
   interpolation, and the checkout's `.env` is a deployment's: run from the
   repository root on 2026-10-19, the quickstart's investigator took the work
   stack's `HOOKPROBE_TOKEN` and every `demo.sh` lookup answered 401 — a
   release that was fine, failing a check that was not testing it. `demo.sh`
   has read that token the way compose does since then (the shell, then
   `./.env`, then the default), but the rule stands: the same `.env` also
   chooses the investigator's runtime and model, and a stranger has neither.

## A note on tags vs. digests

The tag is for humans ("0.6.0"), the digest is for the container runtime ("the
bytes we tested"). Pinning both means `docker compose up` is reproducible *and*
readable. Tests may reference the tag; the shipped quickstart references the
digest.

## Rollback

Images are immutable, so a rollback is re-pinning the quickstart to the previous
release's digest and re-running it. The ledger drives the behaviour, so state is
unchanged by a rollback; only the code moves — which means older code reads rows
newer code wrote, and a release that changes a schema owes its tag note a line on
what that does.

The pipe and the judge migrate additively (a table, or a column with a default),
insert with named columns and read every row as a dictionary, so an older image
opens a newer ledger without an error; what changes is what the older reader
makes of a field it never knew. The investigator rebuilds a case file field by
field, so a field a release adds to a run makes the release before it skip that
file.

Read from the code rather than run, for 0.8.0 back to 0.7.1: nothing fails to
open. A silence a card scoped to one alert quiets its whole source until it
expires, because 0.7.1 does not read the condition. An accepted event still
waiting in the judge's intake table is not replayed until 0.8.0 is back. No
field was added to a run.
