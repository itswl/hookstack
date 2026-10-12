#!/usr/bin/env python3
"""Jira 增量监控：一个项目的全部动静 + 我名下/提及我的工单变化。

- 有变化 → 打印摘要（新建/状态流转/改派/新评论，跳过我自己的操作）
- 无变化 → 无输出（配合盯守循环静默）
- 状态存 ~/.atlassian_watch_state.json；凭证走 ~/.atlassian.env
- 首次运行（无状态文件）回看 6 小时

它同时被两个东西跑：操作者手动跑，和 deploy/docker-compose.work.yml 里的
probe-watch 容器（只读挂载在 /data/jira）。一份代码两个调用方，所以任何改动
两边同时生效——这正是把它收进仓库而不是各留一份拷贝的理由。

要查哪个项目、要不要按 handle 匹配评论，都是部署自己的事，所以走环境变量而
不是写死。一个个人 handle 不该住在一个公开仓库里，这和别的名字是同一条规矩：
ATL_MENTION 为空时不再跳过这一条件，而是从 Jira 账号自己的邮箱取 @ 前的那段
（小写），第一次取到后记在状态文件里，之后不再问；邮箱按隐私设置不可见时，
才退回不含 handle 的那条 JQL。

    ATL_PROJECT   要盯的项目 key（默认 SRE）
    ATL_MENTION   在评论里匹配的 handle；为空则按上面的办法从账号邮箱推
    ATL_ENV_FILE  凭证文件位置，默认 ~/.atlassian.env
    ATL_STATE_FILE 状态文件位置，默认 ~/.atlassian_watch_state.json

后两个是路径而不是常量，因为跑它的进程不止一个：操作者在自己机器上跑，probe-watch
容器里跑过，现在 watch_scan.py 在 timer 容器里跑——而 timer 没有挂 HOME 那个卷，
`~` 底下的写入会直接失败。默认值不变，所以没听说过这两个变量的调用方不受影响。

时区也从账号取。JQL 里不带时区的时间按 Jira 账号设置里的时区解释，写死一个
常量只在账号恰好在那个时区时才对；账号的时区和 accountId 一起问一次、记在
状态里，取不到或不认识时退回 UTC+8。

去重的边界，2026-10-09 在另一处跑着这份脚本的部署上测出来：判断「这条变更报
过没有」必须用「晚于上次报到的时间点」，不能用「不早于」。上次报的最后一条，
时间正好等于记下的时间点；回看窗又有 40 分钟，于是每条变更都在相邻两轮各报一
次，那天的每张工单都连报了。见 fresh()。
"""

import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_FILE = Path(os.environ.get("ATL_STATE_FILE") or os.path.expanduser("~/.atlassian_watch_state.json"))
UTC8 = timezone(timedelta(hours=8))  # 取不到 Jira 账号时区时的退路
OVERLAP = 120  # 秒，窗口重叠防边界丢事件
LOOKBACK_MIN = 40 * 60  # 秒，最小回看窗（>= 2 个调度周期）
REPORTED_TTL = 7 * 86400  # 秒，已汇报记录的保留期


def load_env():
    path = os.environ.get("ATL_ENV_FILE") or os.path.expanduser("~/.atlassian.env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        for line in lines:
            m = re.match(r"\s*(?:export\s+)?(\w+)=['\"]?([^'\"\n]+)['\"]?", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2)


def req(path, params=None):
    url = os.environ["ATL_SITE"] + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    auth = base64.b64encode(f"{os.environ['ATL_EMAIL']}:{os.environ['ATL_TOKEN']}".encode()).decode()
    r = urllib.request.Request(url)
    r.add_header("Authorization", "Basic " + auth)
    r.add_header("Accept", "application/json")
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


def ts(iso):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()


def adf_text(node):
    out = []
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node.get("text", ""))
        if node.get("type") == "hardBreak":
            out.append(" ")
        for c in node.get("content") or []:
            out.append(adf_text(c))
        if node.get("type") in ("paragraph", "heading", "listItem"):
            out.append(" ")
    elif isinstance(node, list):
        out.extend(adf_text(c) for c in node)
    return "".join(out)


def account_zone(name):
    """Jira 账号设置里的时区；没有或不认识时退回 UTC+8。

    不认识的名字 ZoneInfo 抛的是 KeyError 的子类，不是 ValueError——只接后者
    会让一个拼错的时区名把整轮炸掉。
    """
    try:
        return ZoneInfo(name) if name else UTC8
    except (KeyError, ValueError, OSError):
        return UTC8


def fresh(t, last, cutoff):
    """这条变更要不要报：在回看窗里，而且晚于这条工单上次报到的时间点。

    必须是「晚于」，不能是「不早于」。上次报的最后一条，时间正好等于记下的时间
    点；用 >= 它下一轮还在——回看窗有 40 分钟，于是每条变更都报两轮。
    """
    return t >= cutoff and (last is None or t > last)


def mention_from_email(email):
    """账号邮箱 @ 前的那段，小写；邮箱不可见（隐私设置）时为空。"""
    text = str(email or "")
    return text.split("@")[0].strip().lower() if "@" in text else ""


def save_state(state):
    """先写临时文件再改名。这个文件下一轮要读回来：写了一半的 JSON 读不回来，
    之后每一轮都会在读状态这一步挂掉，直到有人手动删掉它。watch_scan.py 的
    scan.json 早就这么写了，这里一直没有跟上。"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE_FILE)


def main():
    load_env()
    now = time.time()
    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    last_check = state.get("jira_last_check", now - 6 * 3600)
    # 至少回看 LOOKBACK_MIN 秒：一轮跑丢了（agent 崩溃/超时），下一轮仍能覆盖
    # 这段窗口。可以放宽是因为下面按「已汇报过的变更时间戳」逐条去重，重复
    # 看见不等于重复汇报。
    cutoff = min(last_check - OVERLAP, now - LOOKBACK_MIN)
    reported = state.get("reported", {})

    # 他是谁、在哪个时区：accountId 用来跳过他自己的操作，handle 用来匹配评论，
    # 时区用来写 JQL 里的时间。只在第一次问一遍，记在状态里；升级前写下的状态
    # 文件缺后两项，补问一次。
    if not state.get("my_account_id") or "mention" not in state or "time_zone" not in state:
        myself = req("/rest/api/3/myself")
        state["my_account_id"] = myself["accountId"]
        state["mention"] = mention_from_email(myself.get("emailAddress"))
        state["time_zone"] = str(myself.get("timeZone") or "")
    me = state["my_account_id"]
    since = datetime.fromtimestamp(cutoff, account_zone(state["time_zone"])).strftime("%Y-%m-%d %H:%M")

    project = os.environ.get("ATL_PROJECT", "SRE").strip() or "SRE"
    mention = os.environ.get("ATL_MENTION", "").strip() or state["mention"]
    jql_safe = f'(project = {project} OR assignee = currentUser()) AND updated >= "{since}" ORDER BY updated ASC'
    # `comment ~` needs an index the account may not have; the original fell back
    # to the safe form on ANY error and that stays. Empty mention skips it
    # entirely rather than sending an empty match.
    jql_full = (
        jql_safe
        if not mention
        else (
            f'(project = {project} OR assignee = currentUser() OR comment ~ "{mention}") '
            f'AND updated >= "{since}" ORDER BY updated ASC'
        )
    )
    try:
        d = req("/rest/api/3/search/jql", {"jql": jql_full, "fields": "summary,status,updated", "maxResults": "30"})
    except Exception:
        d = req("/rest/api/3/search/jql", {"jql": jql_safe, "fields": "summary,status,updated", "maxResults": "30"})

    reports = []
    for hit in d.get("issues", []):
        key = hit["key"]
        i = req(
            f"/rest/api/3/issue/{key}",
            {"fields": "summary,status,assignee,reporter,created,comment", "expand": "changelog"},
        )
        f = i["fields"]
        lines = []
        last = reported.get(key)  # 这条工单已经汇报到哪个时间点了
        newest = last or 0.0
        created = ts(f["created"])
        if fresh(created, last, cutoff) and (f.get("reporter") or {}).get("accountId") != me:
            a = (f.get("assignee") or {}).get("displayName", "未分配")
            lines.append(f"🆕 新建 by {f['reporter']['displayName']}，处理人 {a}")
            # 新建也要推进时间点，否则一张只有「新建」的工单在回看窗里每轮都是新的
            newest = max(newest, created)
        for h in (i.get("changelog") or {}).get("histories", []):
            if not fresh(ts(h["created"]), last, cutoff) or h["author"].get("accountId") == me:
                continue
            newest = max(newest, ts(h["created"]))
            for it in h["items"]:
                if it["field"] in ("status", "assignee", "summary", "duedate", "priority", "description"):
                    frm, to = it.get("fromString") or "-", it.get("toString") or "-"
                    if it["field"] == "description":
                        lines.append(f"✏️ {h['author']['displayName']} 改了描述")
                    else:
                        lines.append(f"🔀 {h['author']['displayName']}: {it['field']} {frm} → {to}")
        for c in (f.get("comment") or {}).get("comments", []):
            c_ts = max(ts(c["created"]), ts(c.get("updated", c["created"])))
            if fresh(c_ts, last, cutoff) and c["author"].get("accountId") != me:
                newest = max(newest, c_ts)
                excerpt = adf_text(c.get("body", {})).strip()[:200]
                lines.append(f"💬 {c['author']['displayName']}: {excerpt}")
        if lines:
            reports.append((key, f["summary"], f["status"]["name"], lines))
            reported[key] = newest

    if reports:
        print("【Jira 变化】")
        for key, summary, status, lines in reports:
            print(f"{key} [{status}] {summary}")
            for ln in lines:
                print(f"  {ln}")

    # 落盘放在输出之后：真正写出去了才推进游标。配合上面的逐 issue 去重，
    # 即使这一轮在此之前挂掉，下一轮的回看窗会重新覆盖，且不会重复汇报。
    state["jira_last_check"] = now
    state["reported"] = {k: v for k, v in reported.items() if v > now - REPORTED_TTL}
    save_state(state)


if __name__ == "__main__":
    main()
