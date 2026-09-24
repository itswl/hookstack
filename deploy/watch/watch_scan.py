#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性扫描：没有新东西就不叫模型。

原来这一段是 brief 里 7518 字节的英文散文，由一个每轮 $0.78 的解释器执行。逐节
看过去，只有一节需要模型：

    §0 查窗口          代码
    §1a 跑 jira_watch  代码（本来就是代码）
    §1b 列会话、比时间戳、按 from_time 取消息   代码
    §2 源不可达怎么办   代码
    §3 什么值得打断他   ← 只有这一节
    §4 怎么投递        代码（watch_report.py）
    §5 把游标写回去     代码（同上，和投递合成一个动作）
    §6 输出格式        代码

所以这里做除 §3 以外的全部，模型只拿到 §3。收益不是省 token：**没有新消息的
一轮现在是 $0**，因为根本不会有事件发出去；而今天每一轮 `[SILENT]` 都要花
$0.25–0.95，因为 agent 必须把整个扫描走完才知道没事。

游标归谁写
    `feeds`（读到哪了）归这个脚本，写在自己的 cursors.json；`reported`（真的
    告诉过他什么）归节点，由 watch_report.py 在发信号的同一次调用里写。

    分成两个文件不是洁癖。一个文件两个写者，agent 写 reported 的同时这里写
    feeds，读-改-写一重叠就丢数据。而且 timer 把节点状态挂成只读是有理由的
    （判官不改证据，见 docker-compose.work.yml），这个脚本跑在 timer 里，本来
    就写不了那个文件。

输出即合同
    什么都没有 → 一个字节都不输出，退出 0。timer 据此跳过这一轮。
    有东西 → 打印给模型看的摘要。**取不到的源也算「有东西」**：一个够不着的
    源不是一个安静的源，这是整套东西存在的理由。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from chat_mcp import Chat, McpError

# 一个文件，一个写者，两个读者：契约检查器从这里读 feeds 和 offered，节点侧的
# watch_report.py 只读 offered。写成两个文件会让「这一轮提供了什么」有两份可能
# 不一致的副本，而它正是用来判断另一份状态对不对的东西。
SCAN = Path(os.environ.get("WATCH_SCAN_FILE") or "/timer-state/scan.json")
# 只在 scan.json 还不存在时读一次，把老状态文件里的 feeds 接过来。没有它，头一轮
# 会把 47 个会话的当前时间戳全当成基线重新播种——正确，但白丢一轮的历史。
SEED = os.environ.get("WATCH_SEED_STATE") or ""
WINDOW = os.environ.get("WATCH_WINDOW") or "09:30-19:30"
DOW = os.environ.get("WATCH_DOW") or "1-5"
EXCLUDE = [s.strip() for s in (os.environ.get("WATCH_EXCLUDE") or "").split(",") if s.strip()]
ME = (os.environ.get("WATCH_ME") or "").strip()
BOTS = [s.strip() for s in (os.environ.get("WATCH_BOTS") or "").split(",") if s.strip()]
SKIP_THREADS = (os.environ.get("WATCH_SKIP_THREADS") or "1") != "0"
JIRA = os.environ.get("WATCH_JIRA_SCRIPT") or ""
# 60 而不是 12：截断要罕见到算异常。上限是 200，一个 20 分钟的窗口里 60 条已经
# 是刷屏了。真截断了下面会明说，因为结果是新的在前，被丢掉的是更早的那些。
PER_CHAT = int(os.environ.get("WATCH_MAX_PER_CHAT") or 60)
# 服务端对这个参数的上限就是 100，传 200 会被整个调用拒掉——而一次被拒的
# 列表调用意味着这一轮完全没看聊天。
FEED_LIMIT = int(os.environ.get("WATCH_FEED_LIMIT") or 100)
# 摘要的字节预算。事件门把正文截到 16000，patrol.sh 会在超出时**拒发**——所以
# 超预算不是「少看几条」，是整轮丢掉。实测：周一早上要拉整个周末，抽查 14 个
# 会话就有 226 条 ≈ 20KB。减去 brief 的 2.3KB 和分隔符，11000 是安全的。
DIGEST_MAX = int(os.environ.get("WATCH_DIGEST_MAX") or 11000)
# 降频名单（.env，逗号分隔——名字是内部标识符，进不了仓库）。名单里的 feed 有新
# 消息不立刻端给模型：先攒 WATCH_BATCH_MINUTES，到期一次性放行。游标不动，攒着
# 的消息一条不丢，只是晚到。为什么需要它：最近 90 轮里 47 轮的全部内容只来自三
# 个高音量群 feed，其中 71% 整轮 [SILENT]——每轮 $0.18 买一句「没事」。名单外的
# 一切（Mentions、单聊、Jira、⚠️）照旧 20 分钟一跳：@提及 14% 的 SILENT 率说明
# 它几乎条条真，不该被降频波及。
BATCH_FEEDS = {t.strip() for t in (os.environ.get("WATCH_BATCH_FEEDS") or "").split(",") if t.strip()}
BATCH_MINUTES = float(os.environ.get("WATCH_BATCH_MINUTES") or 60) * 60.0


def _minutes(hhmm: str) -> int:
    h, _, m = hhmm.strip().partition(":")
    return int(h) * 60 + int(m or 0)


def in_window(now: float) -> bool:
    """他上班的时间。窗口外一个 MCP 调用都不发。

    timer 自己也有 PATROL_HOURS/PATROL_DAYS，那是省钱用的粗筛；这里是权威，
    因为它认得 09:30 这种半点，而 cron 式的小时范围不认。
    """
    t = time.localtime(now)
    lo, _, hi = WINDOW.partition("-")
    d_lo, _, d_hi = DOW.partition("-")
    if not (int(d_lo) <= t.tm_wday + 1 <= int(d_hi or d_lo)):
        return False
    return _minutes(lo) <= t.tm_hour * 60 + t.tm_min <= _minutes(hi)


def when(iso: str) -> float:
    """`send_at` 是 UTC（结尾的 Z）。只跟 unix 游标比，绝不跟本地墙上时间比。"""
    s = (iso or "").strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0.0
    # 万一哪天回包不带时区，按 UTC 认，而不是让 .timestamp() 悄悄按本地时区算
    # ——那会凭空产生八小时的偏移，而且只在一个时区里看得出来。
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def is_fragment(text: str) -> bool:
    """一条没有字母也没有数字的消息是个表情，不是一句话；一个字符的也是。

    实测取到的样本里就有 `"也"` 和 `"、😂"`——brief 用了一整段讲这件事（"a
    single stray character finishing a previous line"），这里两行。

    `"不急，等我看看"` 过得去，这是对的：它算不算信号是判断，不是形状。形状归
    代码，判断归模型，这条线就画在这儿。
    """
    t = (text or "").strip()
    if len(t) <= 1:
        return True
    return not any(unicodedata.category(c)[0] in ("L", "N") for c in t)


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write(path: Path, data: dict) -> None:
    """先写临时文件再改名：这个文件下一轮要被当成基线读，写了一半的 JSON 会让
    下一轮整个把它当不存在，从而重新播种、丢掉全部历史。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


UNREACHABLE_RENOTE_SECONDS = 6 * 3600  # 一个一直够不着的源：说一次，之后每 6 小时提醒一次


def pick_ref(cands: list[dict], name: str) -> str:
    """重名时该用哪个 opaque_ref。

    list_folder_feeds 给的是会话（feed），search_contact 会同时返回同名的联系人和
    会话，还有 `名字:[Text]` 这种 thread。会话名精确相等且 ret_type 是 feed 的那
    一个，就是 feeds 列表里那一行；只有联系人匹配时（单聊的另一面），退到联系人。
    两个以上同类精确匹配时放弃——猜错比取不到更糟。
    """
    for kind in ("feed", "contact"):
        exact = [c for c in cands if c.get("ret_type") == kind and str(c.get("name") or "") == name and c.get("opaque_ref")]
        if len(exact) == 1:
            return str(exact[0]["opaque_ref"])
        if len(exact) > 1:
            return ""
    return ""


def records(chat: "Chat", name: str, args: dict) -> list[dict]:
    """search_chat_records，重名时自己消解。

    聊天服务端对同名的联系人/会话回「Multiple matches…retry with target.ref」。这不是
    取不到，是差一步：search_contact 拿到带 opaque_ref 的候选，挑出会话那一个，
    用 ref 重试。2026-09-07 之前这里直接放弃，两个单聊每 20 分钟被报一次「取不
    到」，游标永远不动。ref 会过期，所以每轮现取，只对重名的那几个多一次调用。
    """
    try:
        return (chat.call("search_chat_records", **args) or {}).get("messages") or []
    except McpError as exc:
        if "Multiple matches" not in str(exc):
            raise
        cands = (chat.call("search_contact", name=name, response_locale="en") or {}).get("candidates") or []
        ref = pick_ref(cands, name)
        if not ref:
            raise McpError(f"search_chat_records: 重名且无法唯一定位 {name!r}（{len(cands)} 个候选）") from exc
        retry = {k: v for k, v in args.items() if k not in ("contact_name", "search_group")}
        return (chat.call("search_chat_records", opaque_ref=ref, **retry) or {}).get("messages") or []


def jira_keys(jira_out: str) -> list[str]:
    """jira_watch 输出里每条 `KEY [status] summary` 的 KEY——它端给模型的工单。"""
    return re.findall(r"^([A-Z][A-Z0-9]+-\d+) \[", jira_out or "", re.M)


def run_jira(notes: list[str]) -> str:
    """jira_watch.py 自己管状态、自己去重、自己跳过他本人的操作。

    没输出不是失败，是没变化。非零退出或者抛栈才是失败，而失败要说出来——它的
    状态文件在崩溃时不落盘，所以下一轮的回看窗会自己补上，但这一轮的沉默不能
    被当成「Jira 很安静」。
    """
    if not JIRA:
        return ""
    try:
        p = subprocess.run(
            [sys.executable, JIRA], capture_output=True, text=True, timeout=180
        )
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append(f"Jira 取不到：{type(exc).__name__} {str(exc)[:200]}")
        return ""
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        notes.append(f"Jira 取不到：exit {p.returncode} {' '.join(tail[-2:])[:300]}")
        return ""
    return (p.stdout or "").strip()


def scan_chat(
    chat: Chat,
    cursors: dict[str, float],
    now: float,
    notes: list[str],
    handed: set[str] | None = None,
    unreachable: dict[str, dict] | None = None,
    pending: dict[str, float] | None = None,
) -> list[tuple[str, list[tuple[float, str, str]]]]:
    """每个会话自上次读到的地方往后取，过滤形状噪音，推进游标。"""
    try:
        feeds = (chat.call("list_folder_feeds", all_chats=True, limit=FEED_LIMIT) or {}).get("feeds") or []
    except McpError as exc:
        notes.append(f"会话列表取不到：{exc}（这一轮完全没看聊天）")
        return []

    findings: list[tuple[str, list[tuple[float, str, str]]]] = []
    for feed in feeds:
        name = str(feed.get("name") or "").strip()
        if not name or name in EXCLUDE:
            continue
        # thread 不是会话。它的 name 是「发信人:正文预览」，六个 thread 可以叫
        # 同一个 `Phil Deng:[Text]`，在一个按名字索引的游标字典里互相覆盖。MCP
        # 用 origin_feed_name 把它们标出来了（50 条里 28 条），丢掉即可——不需要
        # 什么稳定 ID，这是我先前诊断错的地方。
        if SKIP_THREADS and feed.get("origin_feed_name"):
            continue

        latest = when(str(feed.get("last_message_send_at") or ""))
        base = float(cursors.get(name) or 0)
        if not latest or latest <= base:
            continue

        # 降频：名单里的 feed 攒够时长才端出去。判断只有两支——没到期就按住不动
        # （游标不推、消息不取、这轮可以因此整轮 $0），到期就走下面完全正常的路
        # 径。first_seen 记的是内容最早出现的时刻，晚到的消息不重置它，否则一个
        # 热闹的群永远凑不满等待期。
        if name in BATCH_FEEDS:
            first_seen = float((pending or {}).get(name) or 0)
            if not first_seen:
                if pending is not None:
                    pending[name] = now
                continue
            if now - first_seen < BATCH_MINUTES:
                continue
            # 到期放行。取完（或取失败走 unreachable）都在下面清 pending。

        
        args = {
            "contact_name": name,
            # from_time 是闭区间。原样传游标会把「设定这个游标的那条消息」再取
            # 一遍——2026-09-04 就是这么把一条五小时前的 @All 当成新消息发出去的。
            "from_time": str(int(base) + 1),
            "to_time": str(int(now)),
            "limit": PER_CHAT,
        }
        if feed.get("chat_type_label") == "group chat":
            args["search_group"] = True
        try:
            got = records(chat, name, args)
        except McpError as exc:
            # 游标不动：下一轮必须重新覆盖这个窗口。重复一条是麻烦，漏一条不是。
            #
            # 但同一个源连着几十轮够不着，每轮报一次就不是信息了，是噪音——
            # 2026-09-07 两个单聊就这样每 20 分钟进一次群。第一次说、错误变了说、
            # 之后每 6 小时提醒一次并带上持续时长；中间的轮次沉默，状态记在
            # scan.json 的 unreachable 里，恢复了就清掉。
            err = str(exc)
            prev = (unreachable or {}).get(name) or {}
            rounds = int(prev.get("rounds") or 0) + 1
            since = float(prev.get("since") or now)
            renote = (
                not prev
                or prev.get("err") != err[:160]
                or now - float(prev.get("noted_at") or 0) >= UNREACHABLE_RENOTE_SECONDS
            )
            if unreachable is not None:
                unreachable[name] = {
                    "since": since,
                    "rounds": rounds,
                    "err": err[:160],
                    "noted_at": now if renote else float(prev.get("noted_at") or now),
                }
            if renote:
                lasting = f"，自 {time.strftime('%m-%d %H:%M', time.localtime(since))} 起第 {rounds} 轮" if prev else ""
                notes.append(f"{name} 取不到：{err}（游标未推进，下轮重查{lasting}）")
                # 点了名就是交出去了：模型会原样转述这条 ⚠️，契约检查器会看到它
                # 报了这个会话——所以它必须出现在 offered 里，哪怕一个字没读到。
                if handed is not None:
                    handed.add(name)
            continue

        picked: list[tuple[float, str, str]] = []
        for m in got:
            at = when(str(m.get("send_at") or ""))
            if at <= base:
                continue
            who = str(m.get("send_name") or "").strip()
            body = str(m.get("content") or "").strip()
            if ME and who == ME:
                continue  # 他自己说的话不是给他的信号
            if any(b in who for b in BOTS):
                continue
            # 一条图片/文件消息的 content 是空的。按碎片丢掉会让它彻底看不见，
            # 所以换成能看出「有东西但不是文字」的占位——判断仍归模型。
            if not body:
                kind = str(m.get("message_type") or "").strip()
                body = f"[{kind or '非文字消息'}]" if kind != "text/plain text" else ""
            if is_fragment(body):
                continue
            picked.append((at, who, body))

        picked.sort(reverse=True)  # 新的在前，让「先报最老那条」这个错误没法发生
        cursors[name] = latest
        if unreachable is not None:
            unreachable.pop(name, None)  # 又够得着了；下次再断会重新说一次
        if pending is not None:
            pending.pop(name, None)  # 攒的一批已经端出去了；下一批重新起算
        if len(got) >= PER_CHAT:
            notes.append(
                f"{name}: 一次取满 {PER_CHAT} 条，更早的可能没取到"
                f"（结果是新的在前，游标仍推进到最新，否则会一直重报）"
            )
        if picked:
            findings.append((name, picked))
    return findings


def trim(
    findings: list[tuple[str, list[tuple[float, str, str]]]], budget: int
) -> tuple[list[tuple[str, list[tuple[float, str, str]]]], int]:
    """按预算裁到能发出去，**丢最老的，跨会话一起排**。

    丢掉的确实丢了：游标已经推进，它们不会再出现。这是有意的，和 brief 里那条
    早就写死的规矩同源——「报最新的，丢掉更早的；重复一条是麻烦，漏掉真正需要
    回答的那条才是失败」。一个周末的积压里，需要回答的永远是最近那几条。

    另一条路是不推进游标，但那样下一轮会遇到同样的积压，再被拒一次，永远卡住。
    """
    flat = sorted(
        ((at, who, body, name) for name, msgs in findings for at, who, body in msgs),
        reverse=True,
    )
    kept: list[tuple[float, str, str, str]] = []
    used = 0
    for item in flat:
        size = len(item[2].encode("utf-8")) + len(item[1]) + 24  # 正文 + 人名 + 时间戳和装饰
        if used + size > budget:
            break
        kept.append(item)
        used += size

    by_name: dict[str, list[tuple[float, str, str]]] = {}
    for at, who, body, name in kept:
        by_name.setdefault(name, []).append((at, who, body))
    # 保留原来的会话顺序（feeds 的顺序），只是每组少了几条
    out = [(name, by_name[name]) for name, _ in findings if name in by_name]
    return out, len(flat) - len(kept)


def main() -> int:
    now = time.time()
    if not in_window(now):
        return 0  # 一个字节都不输出 = 这一轮不烧钱

    notes: list[str] = []
    jira_out = run_jira(notes)

    scan_file = _read(SCAN)
    cursors: dict[str, float] = dict((scan_file or {}).get("feeds") or {})
    unreachable: dict[str, dict] = dict((scan_file or {}).get("unreachable") or {})
    pending: dict[str, float] = dict((scan_file or {}).get("pending") or {})
    stats: dict[str, dict] = dict((scan_file or {}).get("stats") or {})
    seeding = scan_file is None
    if seeding and SEED:
        cursors = {k: float(v) for k, v in ((_read(Path(SEED)) or {}).get("feeds") or {}).items()}

    chat = Chat()
    findings: list[tuple[str, list[tuple[float, str, str]]]] = []
    handed: set[str] = set()
    if cursors:
        findings = scan_chat(chat, cursors, now, notes, handed, unreachable, pending)
    else:
        # 头一轮立地板：把每个会话的当前时间戳写成基线，聊天这边什么都不报。
        # Jira 不受影响——它有自己的状态和自己的首轮规则，一个源的首轮不该让
        # 另一个源闭嘴。
        try:
            feeds = (chat.call("list_folder_feeds", all_chats=True, limit=FEED_LIMIT) or {}).get("feeds") or []
            cursors = {
                str(f.get("name")): when(str(f.get("last_message_send_at") or ""))
                for f in feeds
                if f.get("name") and not (SKIP_THREADS and f.get("origin_feed_name"))
            }
        except McpError as exc:
            notes.append(f"建立基线失败：{exc}")

    offered_now = {
        **{name: cursors.get(name, now) for name in handed},
        **{key: now for key in jira_keys(jira_out)},
        **{name: cursors.get(name, now) for name, _ in findings},
    }
    for name in offered_now:
        row = stats.setdefault(name, {"offered": 0, "last": 0})
        row["offered"] = int(row.get("offered") or 0) + 1
        row["last"] = now

    _write(
        SCAN,
        {
            "round_at": now,
            "feeds": cursors,
            # 够不着的源和上次提醒的时间——让「说一次、6 小时后再提醒」跨轮成立。
            "unreachable": unreachable,
            # 这一轮真的端给模型看的会话，以及各自读到哪。契约检查器用它判断
            # 「报了一个没人给过它的会话」，watch_report.py 用它决定 reported
            # 写什么值——两者读同一份，所以不会各自算出不同的答案。
            # offered 是「这一轮交给模型的全部主语」，不只是读成功的群：⚠️ 里点名的
            # 读不到的会话（模型会原样转述），和 Jira 段里的工单号（模型用
            # --origin "Jira / KEY" 上报，检查器取 " / " 后半段当主语）。少任何一类，
            # 检查器就会把一次正确的转述判成「报了没人给过它的会话」——2026-09-07
            # 上午它就这样每个 tick 都在喊，六条假违约进了盯守群。
            "offered": offered_now,
            # 降频攒着还没端出去的内容（feed → 内容最早出现时刻）。放行或清空后
            # 消失。写在这里是因为它是 scan.json 的形状之一，不是给模型看的。
            "pending": pending,
            # 每 feed 的累计出场次数。和 offered 同一轮写、同一个键空间，供
            # watch_feed_stats.py 和 reported 侧的 counts 拼出「谁在灌水」——
            # 最近 90 轮里 47 轮的全部内容只来自三个群，这个数该是常设的，
            # 不该每次都从留存的 brief 里反推。
            "stats": stats,
        },
    )

    if not (findings or notes or jira_out):
        return 0

    # 预算要连 notes 和 Jira 一起算：它们都进同一个正文，而 notes 在坏日子里最长
    # （每个够不着的会话一条），正好是最需要预算的那天。
    overhead = len(jira_out.encode("utf-8")) + sum(len(n.encode("utf-8")) + 4 for n in notes)
    findings, dropped = trim(findings, DIGEST_MAX - overhead)
    if dropped:
        notes.append(
            f"摘要超预算，丢掉了 {dropped} 条较早的消息（保留最新的）。游标照常推进，"
            f"所以它们不会再出现——如果这一轮看起来缺了上下文，原因在这里"
        )

    out: list[str] = []
    if notes:
        # 一个够不着的源不是一个安静的源。这一节不是给模型判断的，是给它转述的。
        out.append("## ⚠️ 原样上报，不要判断")
        out += [f"- {n}" for n in notes]
        out.append("")
    if findings:
        out.append("## 新消息（已剔除碎片、表情、你自己的发言和机器人；每组最新在前）")
        for name, msgs in findings:
            out.append("")
            out.append(f"### {name}")
            for at, who, body in msgs:
                stamp = time.strftime("%m-%d %H:%M", time.localtime(at))
                out.append(f"- {stamp} {who}：{body[:400]}")
        out.append("")
    if jira_out:
        out.append("## Jira")
        out.append(jira_out)
    print("\n".join(out).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
