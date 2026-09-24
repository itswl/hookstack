#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jira 增量监控：一个项目的全部动静 + 我名下/提及我的工单变化。

- 有变化 → 打印摘要（新建/状态流转/改派/新评论，跳过我自己的操作）
- 无变化 → 无输出（配合盯守循环静默）
- 状态存 ~/.atlassian_watch_state.json；凭证走 ~/.atlassian.env
- 首次运行（无状态文件）回看 6 小时

它同时被两个东西跑：操作者手动跑，和 deploy/docker-compose.work.yml 里的
probe-watch 容器（只读挂载在 /data/jira）。一份代码两个调用方，所以任何改动
两边同时生效——这正是把它收进仓库而不是各留一份拷贝的理由。

要查哪个项目、要不要按 handle 匹配评论，都是部署自己的事，所以走环境变量而
不是写死。ATL_MENTION 为空时只用不含 handle 的那条 JQL——一个个人 handle 不
该住在一个公开仓库里，这和别的名字是同一条规矩。

    ATL_PROJECT   要盯的项目 key（默认 SRE）
    ATL_MENTION   在评论里匹配的 handle；为空则跳过这一条件
    ATL_ENV_FILE  凭证文件位置，默认 ~/.atlassian.env
    ATL_STATE_FILE 状态文件位置，默认 ~/.atlassian_watch_state.json

后两个是路径而不是常量，因为跑它的进程不止一个：操作者在自己机器上跑，probe-watch
容器里跑过，现在 watch_scan.py 在 timer 容器里跑——而 timer 没有挂 HOME 那个卷，
`~` 底下的写入会直接失败。默认值不变，所以没听说过这两个变量的调用方不受影响。
"""
import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

STATE_FILE = os.environ.get("ATL_STATE_FILE") or os.path.expanduser(
    "~/.atlassian_watch_state.json")
HKT = timezone(timedelta(hours=8))  # Jira 账号时区 Asia/Hong_Kong
OVERLAP = 120  # 秒，窗口重叠防边界丢事件
LOOKBACK_MIN = 40 * 60  # 秒，最小回看窗（>= 2 个调度周期）
REPORTED_TTL = 7 * 86400  # 秒，已汇报记录的保留期


def load_env():
    path = os.environ.get("ATL_ENV_FILE") or os.path.expanduser("~/.atlassian.env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            m = re.match(r"\s*(?:export\s+)?(\w+)=['\"]?([^'\"\n]+)['\"]?", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2)


def req(path, params=None):
    url = os.environ["ATL_SITE"] + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    auth = base64.b64encode(
        f"{os.environ['ATL_EMAIL']}:{os.environ['ATL_TOKEN']}".encode()).decode()
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


def main():
    load_env()
    now = time.time()
    state = {}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE, encoding="utf-8"))
    last = state.get("jira_last_check", now - 6 * 3600)
    # 至少回看 LOOKBACK_MIN 秒：一轮跑丢了（agent 崩溃/超时），下一轮仍能覆盖
    # 这段窗口。可以放宽是因为下面按「已汇报过的变更时间戳」逐条去重，重复
    # 看见不等于重复汇报。
    cutoff = min(last - OVERLAP, now - LOOKBACK_MIN)
    reported = state.get("reported", {})
    since = datetime.fromtimestamp(cutoff, HKT).strftime("%Y-%m-%d %H:%M")

    me = state.get("my_account_id")
    if not me:
        me = req("/rest/api/3/myself")["accountId"]
        state["my_account_id"] = me

    project = os.environ.get("ATL_PROJECT", "SRE").strip() or "SRE"
    mention = os.environ.get("ATL_MENTION", "").strip()
    jql_safe = (f'(project = {project} OR assignee = currentUser()) '
                f'AND updated >= "{since}" ORDER BY updated ASC')
    # `comment ~` needs an index the account may not have; the original fell back
    # to the safe form on ANY error and that stays. Empty mention skips it
    # entirely rather than sending an empty match.
    jql_full = jql_safe if not mention else (
        f'(project = {project} OR assignee = currentUser() OR comment ~ "{mention}") '
        f'AND updated >= "{since}" ORDER BY updated ASC')
    try:
        d = req("/rest/api/3/search/jql",
                {"jql": jql_full, "fields": "summary,status,updated", "maxResults": "30"})
    except Exception:
        d = req("/rest/api/3/search/jql",
                {"jql": jql_safe, "fields": "summary,status,updated", "maxResults": "30"})

    reports = []
    for hit in d.get("issues", []):
        key = hit["key"]
        i = req(f"/rest/api/3/issue/{key}",
                {"fields": "summary,status,assignee,reporter,created,comment",
                 "expand": "changelog"})
        f = i["fields"]
        lines = []
        # 逐 issue 去重下限：这条工单已经汇报到哪个时间点了
        floor = max(cutoff, reported.get(key, 0))
        newest = floor
        if ts(f["created"]) >= floor and (f.get("reporter") or {}).get("accountId") != me:
            a = (f.get("assignee") or {}).get("displayName", "未分配")
            lines.append(f"🆕 新建 by {f['reporter']['displayName']}，处理人 {a}")
        for h in (i.get("changelog") or {}).get("histories", []):
            if ts(h["created"]) < floor or h["author"].get("accountId") == me:
                continue
            newest = max(newest, ts(h["created"]))
            for it in h["items"]:
                if it["field"] in ("status", "assignee", "summary", "duedate",
                                   "priority", "description"):
                    frm, to = it.get("fromString") or "-", it.get("toString") or "-"
                    if it["field"] == "description":
                        lines.append(f"✏️ {h['author']['displayName']} 改了描述")
                    else:
                        lines.append(f"🔀 {h['author']['displayName']}: "
                                     f"{it['field']} {frm} → {to}")
        for c in (f.get("comment") or {}).get("comments", []):
            c_ts = max(ts(c["created"]), ts(c.get("updated", c["created"])))
            if c_ts >= floor and c["author"].get("accountId") != me:
                newest = max(newest, c_ts)
                excerpt = adf_text(c.get("body", {})).strip()[:200]
                lines.append(f"💬 {c['author']['displayName']}: {excerpt}")
        if lines:
            reports.append((key, f["summary"], f["status"]["name"], lines))
            reported[key] = max(newest, floor)

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
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"))


if __name__ == "__main__":
    main()
