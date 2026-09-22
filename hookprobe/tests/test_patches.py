"""A work run's deliverable, lifted out of its report and made reviewable.

The clone the work runner writes to is remote-less, so its commits are
complete exactly when they are invisible. The ```patch block plus this lift
is the return path: the review stays human, and the human gets a diff.
"""

from __future__ import annotations

import asyncio

from hookprobe import patches

DIFF = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,2 +1,4 @@
 # demo-job
+中文说明补全。
+部署与回滚见下。
"""

REPORT = f"""完成了 README 补全，已提交 3ceede3。

```patch
{DIFF}```
"""


def test_a_report_patch_is_extracted_and_counted() -> None:
    got = patches.extract(REPORT)
    assert got.startswith("diff --git")
    assert "中文说明补全" in got
    assert patches.counts(got) == {"adds": 2, "dels": 0, "files": 1}
    assert patches.counts("") == {"adds": 0, "dels": 0, "files": 0}


def test_a_fenced_block_that_is_not_a_diff_is_dropped() -> None:
    """The consumer of this route clicks expecting a diff; prose in the fence
    would arrive where a diff was promised. Drop it rather than store it."""
    assert patches.extract("```patch\nhere is what I did, in prose\n```") == ""
    assert patches.extract("no block at all") == ""
    assert patches.extract("```patch\n```") == ""


def test_the_service_lifts_a_patch_onto_the_run_and_disk(tmp_path):
    """End to end: report carries the block, the service writes the file and
    keeps only bookkeeping on the meta — a diff is an artifact, not metadata."""
    from hookprobe.engine import EngineResult
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    async def scenario():
        settings = make_settings(tmp_path)
        engine = FakeEngine(result=EngineResult(text=REPORT, message_count=1))
        service = RunService(settings, engine, RunStore(tmp_path / "results"))
        service.start({"message": "Title: t\ngo", "sessionKey": "probe:plan-approved:1"})
        for _ in range(300):
            run = service.get("probe:plan-approved:1")
            if run and run.finished:
                return run
            await asyncio.sleep(0.01)
        raise AssertionError("never finished")

    run = asyncio.run(scenario())
    assert {k: run.meta["patch"][k] for k in ("adds", "dels", "files")} == {"adds": 2, "dels": 0, "files": 1}
    # no clone under this workdir: the diff is recorded as unbacked, loudly
    assert run.meta["patch"]["matches"] is None and run.meta["patch"]["commits"] == []
    stored = (tmp_path / "patches" / "probe:plan-approved:1.patch").read_text(encoding="utf-8")
    assert stored.startswith("diff --git")


def test_the_board_offers_the_patch_as_an_artifact() -> None:
    """The name is the size because that is what a board row can use; the text
    is a route away and the clone's history is the ground truth beside it."""
    from hookprobe import work
    from hookprobe.runs import Run

    run = Run(session_key="probe:plan-approved:9", run_id="r9")
    run.text = "done"
    run.meta = {"patch": {"adds": 176, "dels": 1, "files": 1}}
    (item,) = work.resolve([run])
    arts = [a for a in item.artifacts if a["kind"] == "patch"]
    assert arts and arts[0]["ref"] == "probe:plan-approved:9"
    assert "+176/-1 in 1 file(s)" in arts[0]["name"]


def test_the_patch_route_serves_text_and_404s_without_one(tmp_path):
    """The route is the review's front door: text/plain so it opens anywhere,
    and a 404 that MEANS "nothing to review" rather than an error to render."""
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.engine import EngineResult
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    TOKEN = "secret-token"
    settings = make_settings(tmp_path, token=TOKEN)
    engine = FakeEngine(result=EngineResult(text=REPORT, message_count=1))
    service = RunService(settings, engine, RunStore(tmp_path / "results"))

    async def scenario():
        service.start({"message": "Title: t\ngo", "sessionKey": "probe:plan-approved:2"})
        for _ in range(300):
            run = service.get("probe:plan-approved:2")
            if run and run.finished:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("never finished")

    asyncio.run(scenario())
    client = TestClient(create_app(settings, service))
    ok = client.get("/v1/runs/probe:plan-approved:2/patch", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/plain")
    assert ok.text.startswith("diff --git")
    # 无 patch 的 run：404 是"没有可评审的"，不是故障
    gone = client.get("/v1/runs/probe:none/patch", headers={"Authorization": f"Bearer {TOKEN}"})
    assert gone.status_code == 404


DIFF_FENCED_REPORT = """完成 README 补全，已提交。

## 验证
- Maven 构建：跳过，`mvn -version` 退出码 127。

```bash
mvn clean package -DskipTests
```

```diff
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 # demo-job
+定时任务服务。
```
"""


def test_the_lift_accepts_the_diff_fence_the_runner_actually_writes() -> None:
    """The first live run (2026-09-22) was told "a fenced patch block", wrote a
    textbook unified diff under ```diff — the label every renderer colours —
    and GET /v1/runs/{key}/patch answered 404 over a report that carried the
    whole change. The contract names the content, not the label; both
    conventional labels lift, and a ```bash block before it is not mistaken
    for the patch."""
    got = patches.extract(DIFF_FENCED_REPORT)
    assert got.startswith("diff --git a/README.md")
    assert patches.counts(got) == {"adds": 1, "dels": 0, "files": 1}


def test_the_last_valid_diff_block_wins() -> None:
    """A report may quote a diff while explaining what it found; the contract
    puts the run's own patch at the end. And a ```diff fence holding prose is
    skipped, not served."""
    quoted = "```diff\n-old line\n+new line\n```\n"  # not a git diff: no header
    real = "```diff\ndiff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n```\n"
    assert patches.extract(quoted + "words\n" + real).startswith("diff --git a/x")
    assert patches.extract(real + "\nafterthought:\n```diff\nnot a diff\n```").startswith("diff --git a/x")


def _git(repo, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def test_the_patch_is_checked_against_the_commits_that_carry_the_key(tmp_path) -> None:
    """The diff in the report is the agent's claim; the clone is mounted in the
    same container, so the claim is checked. True when the commits carrying
    the run's key diff to exactly the block; False when the block differs;
    None when no commit carried the key — the run that wrote a diff by hand."""
    code = tmp_path / "code"
    repo = code / "demo-job"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "root")
    (repo / "README.md").write_text("# demo-job\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "chore: readme")
    (repo / "README.md").write_text("# demo-job\n定时任务服务。\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "probe:plan-approved:7 补全 README")
    real = _git(repo, "diff", "HEAD~1", "HEAD")

    got = patches.verify(real, code, "probe:plan-approved:7")
    assert got["matches"] is True and got["repo"] == "demo-job" and len(got["commits"]) == 1

    forged = real.replace("定时任务服务", "别的内容")
    assert patches.verify(forged, code, "probe:plan-approved:7")["matches"] is False

    unbacked = patches.verify(real, code, "probe:plan-approved:8")
    assert unbacked["matches"] is None and unbacked["commits"] == []
    assert patches.verify(real, tmp_path / "nowhere", "probe:plan-approved:7")["matches"] is None
