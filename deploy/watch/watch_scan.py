#!/usr/bin/env python3
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

游标什么时候算数（2026-10-19）
    交出摘要的那一轮，新游标不直接落进 `feeds`，而是放在 `staged` 里；Jira 的
    状态同样写在一份暂存副本上。下一轮开头先结算上一轮：timer 在 POST 之后写下
    管道给的事件号（round.json），节点那边 `probe:watch-due:<事件号>` 的运行记录
    说它 completed 就转正，failed、没投出去、管道没路由、超时没结果就丢掉暂存，
    从旧游标重扫，并在摘要里注明。重交两次、离第一次失败满一小时还不行，就只跳过
    第一次失败那一轮的内容，之后的照常交出，跳过的时段写进摘要直到真正送达。
    运行还在跑时这一轮不扫（扫了会把同一批消息交两次）。以前新游标在模型跑之前
    就写死了，模型那轮一失败，这一轮的消息就再也没人看到——另一处跑着这份脚本的
    部署 2026-10-12 就这样丢了两轮。Jira 不跳过：它的回看窗会把那段再报一遍，
    重复一条是麻烦，漏一条不是。

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
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from chat_mcp import Chat, McpError

# 一个文件，一个写者，两个读者：契约检查器从这里读 feeds 和 offered，节点侧的
# watch_report.py 只读 offered。写成两个文件会让「这一轮提供了什么」有两份可能
# 不一致的副本，而它正是用来判断另一份状态对不对的东西。
SCAN = Path(os.environ.get("WATCH_SCAN_FILE") or "/timer-state/scan.json")
# 只在 scan.json 还不存在时读一次，把老状态文件里的 feeds 接过来。没有它，头一轮
# 会把 47 个会话的当前时间戳全当成基线重新播种——正确，但白丢一轮的历史。
SEED = os.environ.get("WATCH_SEED_STATE") or ""
# timer 在每轮 POST 之后写的记录（patrol-timer.sh）：{"posted": bool, "event_id", "outcome", "fired_at"}。
# 下一轮据此判断暂存的游标能不能转正。
ROUND = Path(os.environ.get("WATCH_ROUND_FILE") or str(SCAN.parent / "round.json"))
# 节点的运行记录目录（只读挂载）：唯一能说「那一轮的运行跑完了、失败了、还是根本没发生」的地方。
NODE_RESULTS = Path(os.environ.get("WATCH_NODE_RESULTS") or "/node-state/results")
# 投出去这么久还没有结果就当丢了；节点的运行上限远在这之下。
RUN_TIMEOUT = float(os.environ.get("WATCH_RUN_TIMEOUT") or 1800)
MAX_REPLAYS = 2  # 同一批内容最多再交两次
GIVE_UP_AFTER = 3600.0  # 而且离第一次失败至少满一小时才放弃
# jira_watch.py 的状态文件（它自己也按这个变量找）；交出摘要的那一轮它写的是暂存副本。
JIRA_STATE = Path(os.environ.get("ATL_STATE_FILE") or os.path.expanduser("~/.atlassian_watch_state.json"))
JIRA_STAGED = JIRA_STATE.with_name(JIRA_STATE.stem + ".staged" + JIRA_STATE.suffix)
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
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


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

# 零宽字符和 BiDi 控制字符：人看不见，模型看得见，一句话因此可以在两边长得不一样。
INVISIBLE = dict.fromkeys(
    map(ord, "\u200b\u200c\u200d\u2060\u2062\u2063\u2064\ufeff\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
)


def clean(text: str) -> str:
    """交出去之前把两样东西处理掉：不可见字符删掉，三个反引号换成三个单引号。

    摘要原样拼进这一轮的正文，正文是 markdown——同事消息里的一个 ``` 会把它后面
    的内容关进（或关出）一个代码块；prescan 失败那条路径更是把整段输出放在一个
    代码块里。内容一个字不改，只改形状：判断仍归模型。
    """
    return text.translate(INVISIBLE).replace("```", "'''")


def pick_ref(cands: list[dict], name: str) -> str:
    """重名时该用哪个 opaque_ref。

    list_folder_feeds 给的是会话（feed），search_contact 会同时返回同名的联系人和
    会话，还有 `名字:[Text]` 这种 thread。会话名精确相等且 ret_type 是 feed 的那
    一个，就是 feeds 列表里那一行；只有联系人匹配时（单聊的另一面），退到联系人。
    两个以上同类精确匹配时放弃——猜错比取不到更糟。
    """
    for kind in ("feed", "contact"):
        exact = [
            c for c in cands if c.get("ret_type") == kind and str(c.get("name") or "") == name and c.get("opaque_ref")
        ]
        if len(exact) == 1:
            return str(exact[0]["opaque_ref"])
        if len(exact) > 1:
            return ""
    return ""


def records(chat: Chat, name: str, args: dict) -> list[dict]:
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
    # 这一轮读写的是暂存副本：和聊天游标一起转正或丢弃（见 reconcile）。
    try:
        if JIRA_STATE.exists():
            shutil.copyfile(JIRA_STATE, JIRA_STAGED)
        else:
            JIRA_STAGED.unlink(missing_ok=True)
    except OSError as exc:
        notes.append(f"Jira 取不到：暂存状态写不了 {exc}")
        return ""
    env = {**os.environ, "ATL_STATE_FILE": str(JIRA_STAGED)}
    try:
        p = subprocess.run([sys.executable, JIRA], capture_output=True, text=True, timeout=180, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append(f"Jira 取不到：{type(exc).__name__} {str(exc)[:200]}")
        return ""
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        notes.append(f"Jira 取不到：exit {p.returncode} {' '.join(tail[-2:])[:300]}")
        return ""
    return (p.stdout or "").strip()


def run_record(event_id: object) -> dict | None:
    """节点为管道编号 event_id 的那一轮留下的运行记录，没有就是 None。"""
    key = f"probe:watch-due:{event_id}"
    direct = _read(NODE_RESULTS / f"{key}.json")
    if direct and str(direct.get("session_key") or "") == key:
        return direct
    try:
        for path in NODE_RESULTS.glob("probe:watch-due:*.json"):  # 文件名规则变了也找得到
            data = _read(path)
            if data and str(data.get("session_key") or "") == key:
                return data
    except OSError:
        pass
    return None


def round_outcome(staged: dict, now: float) -> str:
    """上一轮交出去的内容有没有被处理完：done、lost 或 pending（运行还在跑）。"""
    posted = _read(ROUND) or {}
    if not posted.get("posted") or float(posted.get("fired_at") or 0) < float(staged.get("staged_at") or 0):
        return "lost"  # 没投出去，或者 timer 在扫完和投出之间死了
    if posted.get("outcome") != "routed" or posted.get("event_id") in (None, ""):
        return "lost"  # 管道收了但没有路由（风暴熔断、门没开）：没有运行会发生
    status = str((run_record(posted.get("event_id")) or {}).get("status") or "")
    if status == "completed":
        return "done"
    if status == "failed":
        return "lost"
    return "pending" if now - float(posted.get("fired_at") or 0) < RUN_TIMEOUT else "lost"


def reconcile(scan: dict, now: float) -> tuple[dict, str]:
    """结算上一轮：运行跑完了就把暂存的游标转正，没跑完就丢掉暂存、从旧游标重来。

    返回 (新的 scan.json 内容, 结论)。结论是 none（没有暂存）、pending（还在跑，
    这一轮别扫）、done、replay（丢掉暂存，下一轮重扫并注明）或 gave-up（只跳过第
    一次失败那一轮的内容，记下跳过的时段）。Jira 的暂存副本跟着转正或丢弃。
    """
    staged = scan.get("staged")
    if not isinstance(staged, dict) or not staged:
        JIRA_STAGED.unlink(missing_ok=True)  # 没有暂存的摘要却有 Jira 暂存：那一轮扫到一半就死了
        return scan, "none"
    verdict = round_outcome(staged, now)
    if verdict == "pending":
        return scan, "pending"
    scan = {k: v for k, v in scan.items() if k != "staged"}
    if verdict == "done":
        scan["feeds"] = dict(staged.get("feeds") or {})
        if JIRA_STAGED.exists():
            JIRA_STAGED.replace(JIRA_STATE)
        scan.pop("replay", None)
        scan.pop("skipped", None)  # 真正送达了一轮，之前跳过的时段已经说过
        return scan, "done"
    JIRA_STAGED.unlink(missing_ok=True)
    replays = int(staged.get("replays") or 0) + 1
    first_at = float(staged.get("first_at") or staged.get("staged_at") or now)
    first_feeds = dict(staged.get("first_feeds") or staged.get("feeds") or {})
    if replays > MAX_REPLAYS and now - first_at >= GIVE_UP_AFTER:
        scan["feeds"] = first_feeds  # 只跳过第一次失败那一轮的内容；之后的消息下一轮照常交出去
        previous = scan.get("skipped") if isinstance(scan.get("skipped"), dict) else {}
        scan["skipped"] = {
            "from": previous.get("from") or scan.get("round_at") or first_at,
            "to": first_at,
            "attempts": int(previous.get("attempts") or 0) + replays,
        }
        scan.pop("replay", None)
        return scan, "gave-up"
    scan["replay"] = {
        "replays": replays,
        "failed_at": staged.get("staged_at"),
        "first_at": first_at,
        "first_feeds": first_feeds,
    }
    return scan, "replay"


def _hhmm(ts: object) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


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
                f"{name}: 一次取满 {PER_CHAT} 条，更早的可能没取到（结果是新的在前，游标仍推进到最新，否则会一直重报）"
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
    # 先结算上一轮，窗口外也结算：一天最后一轮的失败不该等到第二天早上才处理。
    scan_file = _read(SCAN)
    verdict = "none"
    if scan_file is not None:
        scan_file, verdict = reconcile(scan_file, now)
        if verdict != "none":
            _write(SCAN, scan_file)
    if not in_window(now):
        return 0  # 一个字节都不输出 = 这一轮不烧钱
    if verdict == "pending":
        return 0  # 上一轮的运行还在跑：现在扫会把同一批消息交两次

    notes: list[str] = []
    jira_out = run_jira(notes)

    cursors: dict[str, float] = dict((scan_file or {}).get("feeds") or {})
    replay: dict = scan_file.get("replay") if scan_file and isinstance(scan_file.get("replay"), dict) else {}
    skipped: dict = scan_file.get("skipped") if scan_file and isinstance(scan_file.get("skipped"), dict) else {}
    unreachable: dict[str, dict] = dict((scan_file or {}).get("unreachable") or {})
    pending: dict[str, float] = dict((scan_file or {}).get("pending") or {})
    stats: dict[str, dict] = dict((scan_file or {}).get("stats") or {})
    seeding = scan_file is None
    if seeding and SEED:
        cursors = {k: float(v) for k, v in ((_read(Path(SEED)) or {}).get("feeds") or {}).items()}

    chat = Chat()
    findings: list[tuple[str, list[tuple[float, str, str]]]] = []
    handed: set[str] = set()
    committed: dict[str, float] = dict(cursors)  # 转正前 feeds 里仍然是这份
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

    quiet = not (findings or notes or jira_out)
    if replay and not quiet:
        notes.append(
            f"上一轮（{_hhmm(replay.get('failed_at'))}）交出的内容没有被处理完，这一轮从那之前的位置重新扫了一遍，"
            f"所以下面可能有之前出现过的消息"
        )
    if skipped and not quiet:
        notes.append(
            f"{_hhmm(skipped.get('from'))} 到 {_hhmm(skipped.get('to'))} 之间的消息连续 {skipped.get('attempts')} 次"
            f"都没有处理完，已经跳过，不会再出现；要看就去聊天里翻"
        )
    record: dict = {
        "round_at": now,
        # 上一轮的 offer 和它的时钟，原样留一轮：一次跑过了下一跳的运行（排队、
        # 慢模型）投递时读到的是这一轮的 offer，而它拿到的是上一轮的。签名器和
        # watch_report.py 两边都认，各按自己那轮计数。再往前不留：运行上限 30
        # 分钟，跨不过两跳。
        "previous": {
            "round_at": float((scan_file or {}).get("round_at") or 0),
            "offered": dict((scan_file or {}).get("offered") or {}),
        },
        # feeds 在下面按这一轮有没有叫模型决定：转正过的，还是直接算数的。
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
    }
    if quiet:
        # 没叫模型，没有什么会丢：新游标直接算数，Jira 的暂存副本也直接转正。
        record["feeds"] = cursors
        if skipped:
            record["skipped"] = skipped
        try:
            if JIRA_STAGED.exists():
                JIRA_STAGED.replace(JIRA_STATE)
        except OSError:
            pass
        _write(SCAN, record)
        return 0
    # 叫了模型：feeds 里仍是转正过的游标，新游标暂存，下一轮结算（见 reconcile）。
    record["feeds"] = committed
    if skipped:
        record["skipped"] = skipped
    record["staged"] = {
        "feeds": cursors,
        "staged_at": now,
        "replays": int(replay.get("replays") or 0),
        "first_at": replay.get("first_at") or now,
        "first_feeds": replay.get("first_feeds") or cursors,
    }
    _write(SCAN, record)

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
    print(clean("\n".join(out).strip()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
