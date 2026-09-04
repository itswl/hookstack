---
title: hookstack
description: 把 agent 放进生产,并且事后算得清账 —— 每一跳都签名、计价、可回放的交接总线,外加一个完全可以单独使用的只读 agent runner。
---

[English](../) · **中文**

**把 agent 放进生产,并且事后算得清账。**

三个各做一件事的服务 —— 内容无关的管道、判官、只读调查器 —— 用配置文件而不是
代码连起来。让它不止是一个路由器的,是每次交接周围的东西:每一跳都签名、重试、
死信、计价、可回放,每个 agent 都跑在一份凭证、一个预算、和一张写死了"它能做
什么"的清单后面。

那个 [agent runner](#hookprobe) 本身就有用,无论你关不关心告警。

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

## 把 agent 放在会花钱的地方

这里的 agent 被当作一个**不可信的网络服务**:它花钱、它读别人写的文字、它握着
凭证。下面每一条的存在,都是因为这三件里至少有一件是真的。

| | |
| --- | --- |
| **交接带签名** | 每道门校验带时间戳的 HMAC;每个节点有自己的密钥、预算和守卫 |
| **能做什么是一张闭集清单** | `HOOKPROBE_MCP_TOOLS` 列出这个实例可以调用的 MCP 工具,**留空则一个都不许调**。挂载一个 server 不等于授予它的工具 —— 没有哪个 server 因为你希望它只读就真的只读,一个聊天 server 会把 `send_message` 和 `search_messages` 放在一起 |
| **能得出什么结论也是闭集** | 一个能左右路由的裁决,只能从操作者声明过的词表里挑,不能自由书写 |
| **只读是构造出来的** | 写操作动词在执行前被拒(`aws` 命令**除非是读否则拒绝**)、只读凭证才是真正的边界、还有一道钩子拦住"一次运行改写自己下一次的指令" |
| **花销有上限** | 窗口内花超了就拒绝新的自主运行,而且**每次拒绝都自己报出来**,不会静默 |
| **改路由之前先看清图** | `GET /topology` 只凭配置渲染出门、阶段落点、出口,并点出这个形状隐含的风险:没有路由能到的门、没有东西喂的出口、以及会把 brain 自己的输出喂回给它的回环 |
| **事后能翻旧账** | `GET /trace/{id}` 回放每一跳的双向字节 —— 只存 body 从不存 headers,因为 headers 带签名和令牌 |

诚实的完整版本,**包括每条边界挡不住什么**,在
[docs/containment.md](https://github.com/itswl/hookstack/blob/main/docs/containment.md)。
一条只被"它能挡住什么"描述过的守卫,会被拿去信任它从未声称过的事情。

### 同一份代码,两张完全不同的图

这个仓库跑着两套部署,共享每一行服务代码。一套把监控平台的告警穿过三个判官送成
一张卡片;另一套把操作者自己的工作信号 —— 聊天和工单 —— 穿过一个盯守器送到两个
不同的群,并且在"确实是活儿"那条分支上挂一个规划器。**两边都没有多写一行 Python**,
而它们分歧的四个地方各有各的理由:
[docs/deployments.md](https://github.com/itswl/hookstack/blob/main/docs/deployments.md)。

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
