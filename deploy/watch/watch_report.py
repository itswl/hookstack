#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发一条信号，并在同一次调用里记下「这个会话已经汇报到哪」。

brief 里这是两节：§4 说怎么投递，§5 说把游标写回去。2026-09-04 16:40 的那一轮
做了前者、没做后者，下一轮读到同一个旧游标，把同一条消息又发了一遍。两件事写
在两个段落里，只有第一件有可观察的效果——所以这里把它们合成一个动作，做不到
一半。

    echo '{"title":"...","detail":"...","level":"low","kind":"note"}' \
        | python3 watch_report.py --conversation "BCP-SRE"

`--conversation` 原样抄扫描摘要里的会话名。游标值不用填：这一轮扫描器提供了哪
些会话、各自读到哪，都在 scan.json 里，这里自己查。让模型抄数字，正是这次要
去掉的那件事。

不是从会话来的发现（Jira 工单、扫描器自己报的故障）没有游标可推进，用：

        | python3 watch_report.py --origin "Jira / SRE-1234"

两种模式一个出口。让模型在「有游标的」和「没游标的」之间自己选另一个脚本，就
是在把一个分支交回给它——而它在 §4/§5 上已经错过一次了。

`origin` 也在这里拼，不由调用方给。scripts/assert_node_contract.py 靠 " / " 分
隔符从 origin 里认出会话名，一个拼错的 origin 会让契约检查静默失效——那正是它
要抓的那类失败。

顺序是先投递、后记账。投出去了但记账失败，下一轮重复一条，是麻烦；反过来记了
账没投出去，是一条永远不会再出现的信号，是这套东西存在的理由要防的事。

退出码沿用投递脚本的：0 送到，1 门拒了或够不着，2 输入不对。非零必须在本轮
输出里原样说出来。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCAN = Path(os.environ.get("WATCH_SCAN_FILE") or "/scan/scan.json")
STATE = Path(os.environ.get("WATCH_REPORTED_STATE") or "")
POSTER = os.environ.get("WATCH_SIGNAL_POSTER") or "/data/bin/post_watch_signal.py"
# 契约检查器认的那个分隔符，和它写在同一个常量意图里：见 ORIGIN_SEPARATOR。
ORIGIN_PREFIX = os.environ.get("WATCH_ORIGIN_PREFIX") or "chat"


def die(msg: str, code: int = 2) -> int:
    print(msg, file=sys.stderr)
    return code


def admitted(scan: dict | None, conversation: str) -> tuple[float | None, str]:
    """这个会话该记的游标，或者 None 和拒绝的理由。

    None 加空理由 = 扫描文件没有 offered（老格式或读不到）：不检查，也不记账。
    这一轮的 offer 优先，其次上一轮的（watch_scan.py 写在 previous 里）：一次跑过了
    下一跳的运行拿到的是上一轮的 offer，投递时文件已经翻页，拿它那一轮的游标记账
    才是对的——签名器也按同一条规则放行。
    """
    known = (scan or {}).get("offered")
    if known is None:
        return None, ""
    if conversation in known:
        return float(known[conversation]), ""
    previous = ((scan or {}).get("previous") or {}).get("offered") or {}
    if conversation in previous:
        return float(previous[conversation]), ""
    # 会话名抄错了。现在拦住，比让契约检查在下一轮报一个看不懂的违约好——它按会话
    # 名认主语，名字错了它会安静地什么都不检查。
    return None, f"这一轮没有提供过会话 {conversation!r}。可用的是：{sorted(known)}（上一轮：{sorted(previous)}）"


def main() -> int:
    argv = sys.argv[1:]
    conversation = argv[argv.index("--conversation") + 1].strip() if "--conversation" in argv else ""
    origin = argv[argv.index("--origin") + 1].strip() if "--origin" in argv else ""
    if not conversation and not origin:
        return die(
            "用法：echo '<JSON>' | watch_report.py --conversation \"<会话名>\"\n"
            "      echo '<JSON>' | watch_report.py --origin \"Jira / SRE-1234\""
        )

    try:
        signal = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        return die(f"输入不是 JSON：{exc}")
    if not isinstance(signal, dict) or not signal.get("title"):
        return die("信号必须是个带 title 的 JSON 对象")

    # None = 扫描文件没有 offered（老格式或读不到）→ 跳过检查；{} = 这一轮什么都没
    # 提供 → 任何会话名都是抄错的。原来写成 `if offered and ...`，空表被当成
    # 「没法检查」放行了，一个空轮次里编出来的会话名就这样溜进了账本。和
    # scripts/assert_node_contract.py 的语义对齐：缺键才跳，空表要判。
    cursor: float | None = None
    if conversation:
        scan: dict | None = None
        try:
            scan = json.loads(SCAN.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError) as exc:
            # 不致命：投递照做，只是记不了账。说出来，因为下一轮会重复这一条。
            print(f"⚠️ 读不到 {SCAN}（{exc}）：这一条会投出去但记不了账", file=sys.stderr)
        cursor, why = admitted(scan, conversation)
        if why:
            return die(why)

    signal.setdefault("origin", origin or f"{ORIGIN_PREFIX} / {conversation}")
    signal.setdefault("level", "low")
    signal.setdefault("kind", "note")

    posted = subprocess.run(
        [sys.executable, POSTER],
        input=json.dumps(signal, ensure_ascii=False),
        capture_output=True,
        text=True,
    )
    if posted.returncode != 0:
        sys.stderr.write(posted.stderr)
        return posted.returncode
    sys.stdout.write(posted.stdout)

    if not STATE:
        return 0
    try:
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    if conversation and cursor is not None:
        state.setdefault("reported", {})[conversation] = cursor
    # 每 feed 的累计投递数。游标只留最新值，答不了「这个群一共给我送过多少条」
    # ——而那正是判断高音量低信号 feed 要用的数（扫描器侧的 stats 记另一半）。
    key = conversation or origin
    if key:
        row = state.setdefault("counts", {}).setdefault(key, {"delivered": 0, "last": 0})
        row["delivered"] = int(row.get("delivered") or 0) + 1
        row["last"] = time.time()
    tmp = STATE.with_suffix(STATE.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE)
    except OSError as exc:
        # 信号已经送到了，所以退出码保持 0——但这次记账没成，下一轮会重复。
        print(f"⚠️ 信号已送达，但 reported 没写成（{exc}）：下一轮可能重复", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
