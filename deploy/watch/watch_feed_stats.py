#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每个 feed 的出场与投递：谁在灌水，谁在喂你。

2026-09-20 从留存的 brief 里反推出一次性的答案（最近 90 轮：47 轮的全部内容只来
自三个群 feed，其中 71% 整轮 [SILENT]），那个数决定了 WATCH_BATCH_FEEDS 名单。
但反推只对过去成立——从那之后，两侧各自累计：

    扫描器   scan.json 的 stats[name].offered   每 feed 每轮出场 +1
    投递侧   状态文件的 counts[key].delivered   每发一条信号 +1

本脚本把两份拼成一张表。宿主机直接跑，不进容器：

    python3 data/watch/watch_feed_stats.py

读的是两个只读文件，不写任何东西。计数从两侧代码上线那刻起算，窗口起点在表尾
打印——头一天数字小是正常的，看比例不看绝对值。

Jira 的键形如 "Jira / SRE-1234"，一票一条；出场侧同一键每轮 +1。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCAN = Path(os.environ.get("WATCH_SCAN_FILE") or ROOT / "work-data/watch-timer-state/scan.json")
def _reported_state() -> Path:
    """Where the node records what it has already reported.

    `WATCH_REPORTED_STATE` wins, as in the compose. The fallback DISCOVERS the
    file instead of naming it: its name carries the chat tool's own name, which
    belongs in `.env` and not in a tracked file — the same reason the compose
    builds this path from `WATCH_STATE_FILE`. Discovery also fails usefully:
    naming a file that is not there makes `_load` return {} and every count
    print as zero, which reads like a real answer.
    """
    override = os.environ.get("WATCH_REPORTED_STATE")
    if override:
        return Path(override)
    found = sorted((ROOT / "work-data/probe-watch").glob("*_watch_state.json"))
    if len(found) == 1:
        return found[0]
    problem = "no *_watch_state.json" if not found else f"{len(found)} candidates: {[p.name for p in found]}"
    print(f"set WATCH_REPORTED_STATE: {problem} under work-data/probe-watch", file=sys.stderr)
    raise SystemExit(2)


STATE = _reported_state()


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读不到 {path}：{exc}", file=sys.stderr)
        return {}


def main() -> int:
    scan = _load(SCAN)
    state = _load(STATE)
    stats = scan.get("stats") or {}
    # 投递侧记的是 origin 键（"Jira / SRE-22"），出场侧记的是裸键（jira_keys 的
    # 输出，"SRE-22"）。不归一，同一张票拆成两行、rate 全部落空——第一天的表就
    # 把送了三条信号的 SRE-22 显示成 0%，而表底的提示正让人去找 0% 的行降频。
    counts = {}
    for key, val in (state.get("counts") or {}).items():
        counts[key.removeprefix("Jira / ")] = val
    pending = scan.get("pending") or {}
    unreachable = scan.get("unreachable") or {}
    cursors = scan.get("feeds") or {}

    names = sorted(set(stats) | set(counts), key=lambda n: -int((stats.get(n) or {}).get("offered") or 0))
    if not names:
        print("还没有任何计数（两侧代码上线后的第一轮才会开始累计）。")
        return 0

    now = time.time()
    print("%-34s %7s %6s %6s %9s %s" % ("feed", "offered", "deliv", "rate", "last_msg", "状态"))
    for name in names:
        off = int((stats.get(name) or {}).get("offered") or 0)
        dliv = int((counts.get(name) or {}).get("delivered") or 0)
        rate = f"{100 * dliv / off:.0f}%" if off else "-"
        cur = float(cursors.get(name) or 0)
        age = f"{(now - cur) / 3600:.0f}h前" if cur else "-"
        flags = []
        if name in pending:
            flags.append(f"攒着{(now - float(pending[name])) / 60:.0f}min")
        if name in unreachable:
            flags.append("够不着")
        print("%-34s %7d %6d %6s %9s %s" % (name[:34], off, dliv, rate, age, " ".join(flags)))

    tot_off = sum(int((v or {}).get("offered") or 0) for v in stats.values())
    tot_dliv = sum(int((v or {}).get("delivered") or 0) for v in counts.values())
    start = min(
        [float((v or {}).get("last") or 0) for v in stats.values()]
        + [float((v or {}).get("last") or 0) for v in counts.values()]
        + [now]
    )
    print(
        f"\n合计 offered={tot_off} delivered={tot_dliv}（{100 * tot_dliv / tot_off:.0f}%）"
        f" · 计数自 {time.strftime('%m-%d %H:%M', time.localtime(start))} 起"
    )
    print("高 offered 低 rate 的行就是降频候选：WATCH_BATCH_FEEDS 加名字，.env 改。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
