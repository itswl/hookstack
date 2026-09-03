---
title: hookstack
description: 三个各司其职的告警服务 —— 外加一个完全可以单独使用的只读 agent runner。
---

[English](../) · **中文**

把告警处理拆成三个各做一件事的服务 —— 外加一个
[agent runner](#hookprobe),它本身就有用,
无论你关不关心告警。

MIT 协议,`docker compose up`。→ **[github.com/itswl/hookstack](https://github.com/itswl/hookstack)**

---

## hookprobe:一次 agent 运行,包在一个 HTTP 契约里 {#hookprobe}

你 POST 一个任务。hookprobe 跑一次带工具的 agent 会话(Claude Agent SDK:bash、
MCP 服务器、联网搜索、`SKILL.md` 技能),然后把报告交给来轮询的人。没有消息通道,
没有设备配对,没有聊天历史。

它说的是 OpenClaw 兼容的方言 —— 已经把那个 gateway 当分析后端的客户端,改个 URL
就能切过来。

```bash
git clone https://github.com/itswl/hookstack && cd hookstack
printf 'HOOKPROBE_TOKEN=change-me\nANTHROPIC_API_KEY=sk-ant-...\n' > .env
docker compose --env-file .env -f hookprobe/deploy/docker-compose.yml up -d --build

curl -s -X POST localhost:8088/hooks/agent \
  -H "Authorization: Bearer change-me" -H 'Content-Type: application/json' \
  -d '{"message":"哪些进程在监听,分别监听什么端口?","sessionKey":"demo:1"}'

curl -s -H "Authorization: Bearer change-me" localhost:8088/sessions/demo:1/final
```

### 三个不一样的地方

**它会终止。** `/final` 只返回 `202`,或者一个确定终态的 `200` —— 包括运行崩溃
或超时的情况,那时报告的 `root_cause` 会写明是 runner 挂了。调用方第一次读到确认
就能落库,不用自己发明「连续三次答案没变就算完了」这种稳定性启发式。

**agent 改不了那些会左右下一次运行的东西。** `.claude/`(技能、角色、设置)、
`CLAUDE.md` 和审计日志对它是关闭的 —— 由一个 PreToolUse hook 直接拒绝写入,再加上
每次运行前后比对所有输入文件的摘要,因为这两者的失效方式不一样。没有这道防线,
一条注入进 `.claude/skills/` 的指令就活过了读到它的那次运行,以后会以「运维自己的
runbook」的身份回来。

**跑完的运行会留下 runbook。** 一次完成的调查会把自己的记录蒸馏成 `SKILL.md` ——
由服务写入,永远不经过 agent 的工具。同一个条件的第二次调查是**追加一个 case**
而不是替换原有内容,而且每一次写入(无论来自运行还是来自人)都会先把被覆盖的版本
快照存档。

![会话控制台](../img/hookprobe-sessions.png)

![一次调查的每一个工具调用,实时](../img/hookprobe-live-feed.png)

![一次运行为自己蒸馏出的诊断 runbook](../img/hookprobe-skills.png)

---

## 完整的三件套

| 组件 | 职责 | 刻意不做 |
| --- | --- | --- |
| **hookrelay** | 管道 —— 把每种上游方言适配进来、每种通道格式渲染出去,并且全程记账 | 理解内容,或做判断 |
| **hookjudge** | 判官 —— 一个事件进,一个判定出,四条按成本排序的路径 | 渲染卡片,或了解通道 |
| **hookprobe** | 调查员 —— 对值得的事件跑一次只读 agent 运行:告警问「哪里坏了」,工作项问「具体怎么做」 | 接收告警,或发送通知 |

```
上游告警源(Grafana / Alertmanager / 云监控 …)
      │
      ▼
  hookrelay :8100 ──► hookjudge :8200 ──► hookrelay ──► 飞书 / 钉钉 / 企微
  (管道:适配+路由+账本)  │ (判官:判定+成本)      (格式化并投递)
                          │
                          └──► hookprobe :8088 ──► hookrelay ──► 同样的通道
                               (调查员:只读)      /hook/probe-notify

已经自己判过的源走终端路由,不再进判官:

  你的源 ──► hookrelay ──┬──► 你指定的通道   (不判:它已经定了)
  (签名的门)             └──► hookprobe :8088 (它说要查才查)
```

### 进管道的不一定都是告警

判官在告警上才划算 —— 严重度、恢复、抖动、四条按成本排序的路径。一个**已经**做完判断的源(比如一个只转发「这件事需要人」的盯守,或者一个只发要紧事的系统),再判一次得不到任何东西,而且会被用不合适的词汇去判。

所以路由可以是终端的:你自己的一扇签名的门,直达你指定的通道,绕过判官。它仍然拿到管道的账本、重试和死信 —— 那些是关于投递的部分,不是关于内容的部分。

调查员从那里依然够得着,而且当事件是工作而不是故障时,它问的是另一个问题:现在有什么、缺什么、步骤是什么、结果怎么验证 —— 以及它看不见的东西,是列出来而不是猜出来。两种情况下它都不提出任何可执行的动作。

![hookrelay 的账本:每条消息都有交代,每次投递都有结果](../img/hookrelay-ledger.png)

![hookjudge 的状态页:四种判定和它们各自的路径](../img/hookjudge-status.png)

上面每一张截图都取自一次从零开始的本地 Docker 运行 —— 不是效果图。

---

## 继续读

- [完整的叙述性总览](https://github.com/itswl/hookstack/blob/main/OVERVIEW.md)(英文)
- [hookprobe 参考文档](https://github.com/itswl/hookstack/blob/main/hookprobe/README.md)(英文)
- [把三件套一起跑起来](https://github.com/itswl/hookstack/blob/main/STACK.md)(英文)
- [WebhookWise](https://itswl.github.io/WebhookWise/zh/) —— 这几个服务生长出来的那个自托管告警平台
