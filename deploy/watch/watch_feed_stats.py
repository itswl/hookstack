#!/usr/bin/env python3
"""每个 feed 的出场与投递：谁在灌水，谁在喂你。

2026-09-20 从留存的 brief 里反推出一次性的答案（最近 90 轮：47 轮的全部内容只来
自三个群 feed，其中 71% 整轮 [SILENT]），那个数决定了 WATCH_BATCH_FEEDS 名单。
但反推只对过去成立——从那之后，两侧各自累计：

    扫描器   scan.json 的 stats[name].offered        每 feed 每轮出场 +1
    投递侧   签名器账本 signals.jsonl 的 signal.signed   每签一条信号一行

本脚本把两份拼成一张表。宿主机直接跑，不进容器：

    python3 deploy/watch/watch_feed_stats.py

读的是两个只读文件，不写任何东西。计数从两侧代码上线那刻起算，窗口起点在表尾
打印——头一天数字小是正常的，看比例不看绝对值。

投递侧 2026-10-05 起改读签名器的账本：它由 agent 够不着的进程写，每行带
subject（" / " 之后的会话名，Jira 的就是裸票号），和扫描器的键空间直接对上。
之前读的是节点状态文件里 agent 自己记的 counts，那是一份自述。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCAN = Path(os.environ.get("WATCH_SCAN_FILE") or ROOT / "work-data/watch-timer-state/scan.json")
LEDGER = Path(os.environ.get("WATCH_SIGNER_LEDGER") or ROOT / "work-data/watch-signer/signals.jsonl")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读不到 {path}：{exc}", file=sys.stderr)
        return {}


def delivered(ledger: Path) -> dict[str, dict]:
    """每个 subject 签过几条、最近一条何时——签名器账本里 signal.signed 的行。
    subject 是 " / " 之后的会话名；Jira 的就是裸票号，和扫描器的键直接对上。"""
    counts: dict[str, dict] = {}
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"读不到 {ledger}：{exc}", file=sys.stderr)
        return counts
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "signal.signed" or not row.get("subject"):
            continue
        entry = counts.setdefault(str(row["subject"]), {"delivered": 0, "last": 0.0})
        entry["delivered"] += 1
        entry["last"] = max(float(entry["last"]), float(row.get("ts") or 0))
    return counts


def main() -> int:
    scan = _load(SCAN)
    stats = scan.get("stats") or {}
    counts = delivered(LEDGER)
    pending = scan.get("pending") or {}
    unreachable = scan.get("unreachable") or {}
    cursors = scan.get("feeds") or {}

    names = sorted(set(stats) | set(counts), key=lambda n: -int((stats.get(n) or {}).get("offered") or 0))
    if not names:
        print("还没有任何计数（两侧代码上线后的第一轮才会开始累计）。")
        return 0

    now = time.time()
    print(f"{'feed':<34} {'offered':>7} {'deliv':>6} {'rate':>6} {'last_msg':>9} 状态")
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
        print(f"{name[:34]:<34} {off:>7d} {dliv:>6d} {rate:>6} {age:>9} {' '.join(flags)}")

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
