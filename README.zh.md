# hook 系列告警家族总览

一组围绕"告警怎么被处理"生长出来的小服务，以及它们和 WebhookWise 的关系。家族的分工哲学是一件事一个组件：**hookrelay** 是管道，把各家监控的方言适配进来、把结果按各渠道的格式送出去；**hookjudge** 是判官，一条事件、一个裁决、一笔账；**hookprobe** 是调查员，对值得深挖的告警做一次带工具的只读 agent 调查；**WebhookWise** 保持大而全，渠道、卡片、知识库、看板都在它那里，是家族里的另一个大脑。

文中所有截图都来自 2026-08-12 真实运行的服务与真实数据，不是设计稿。三个服务同住本仓库，各自完全独立（自己的包、测试、gate、Dockerfile、CI）；WebhookWise 是独立仓库。

```
上游告警源(Grafana / Alertmanager / 云监控 …)
      │
      ▼
  hookrelay :8100 ──► hookjudge :8200 ──► hookrelay ──► 飞书 / 钉钉 / 企微
  (管道:适配+路由+账本)    (判官:裁决+成本)     (格式化投递)

  WebhookWise (大而全编排, 另一个大脑)
      │ 深度分析(OpenClaw 兼容合同)
      ▼
  hookprobe :8088 (调查员:一次只读 agent 调查, 替代整套 OpenClaw 网关)
```

## 家族分工

| 组件 | 角色 | 一句话职责 | 刻意不做 |
| --- | --- | --- | --- |
| [`hookrelay/`](hookrelay) | 管道 | 把每种上游方言适配成统一事件，按路由送去大脑，再把裁决渲染成各渠道格式投递，全程记账 | 不理解内容，不做判断 |
| [`hookjudge/`](hookjudge) | 判官 | 一进（事件）一出（裁决），四条路由按成本排序：recovery、reuse、ai、rule | 不渲染卡片，不管渠道 |
| [`hookprobe/`](hookprobe) | 调查员 | 接一个分析任务，跑一次带工具的只读 agent 调查，交回根因报告；会话可追问，经验会沉淀 | 不接告警，不发通知 |
| WebhookWise | 编排 | 六路上游接入、渠道渲染、知识库、事件管理、看板；深度分析这条腿交给 hookprobe | 保持大而全是明确决策 |

拆分的道理在于：一个要渲染飞书卡片的大脑，就得懂飞书的卡片 schema，然后是企微的、钉钉的——这些活属于管道。把它们挪进管道，大脑才可以被替换、被比较，而两端不动。所以家族里可以同时存在两个大脑：WebhookWise 大而全，hookjudge 极简，后者的极简是对照而不是批评；管道的扇出与汇聚，本来就是为"多个处理系统加工同一信息"准备的。

## hookrelay：管道

管道是唯一的告警前门。上游方言在配置里声明式适配：占位符从原始载荷抽取标题、正文、级别，level_map 把 alerting、firing 这类各家用词翻译成统一级别；路由表决定事件去哪——默认一切进大脑，而判官的回传走更高优先级的路由直达渠道并就此停住，防止裁决被再次送去裁决。每条消息有交代：排队、送达、死信在账本页一眼可见，每个事件都能点开查看完整的决策链。

下图是本机演示打完四发告警后的账本：inbound 与 alertmanager 两个前门进来的事件被路由到 to-judge，判官回传的 judge-notify 被路由到 ops-feishu 和 ops-dingtalk 两个渠道，12 次投递全部送达、零死信。

![hookrelay 账本页：每条消息有交代，每次投递有结局](docs/img/hookrelay-ledger.png)

## hookjudge：判官

判官只做一件事：收一条标准化事件，给一个裁决（重要性、分类、一句话摘要），回传给管道，同时记一笔账。它的成本策略直接写在路由顺序里：**recovery**（恢复事件继承其告警的裁决，免费）→ **reuse**（同一状况的复述沿用上一次 AI 裁决，免费）→ **ai**（真正花钱问模型）→ **rule**（规则兜底）。省钱的关键不是把模型调便宜，而是让大多数事件根本走不到 ai 这一步。

下图是那四发演示告警的裁决账本：支付网关 5xx 第一次出现走 ai 付费，同一状况的复述命中 reuse 免费；磁盘告警走 ai 付费，随后的恢复走 recovery 免费——4 条裁决、付费比例 50%、总花费 $0.000524、回传失败 0。这套数字和 [STACK.md](STACK.md) 里承诺的演示结果完全一致。

![hookjudge 状态页：四条裁决与各自路由，付费比例 50%](docs/img/hookjudge-status.png)

## hookprobe：调查员

hookprobe 的出身要从 WebhookWise 说起：深度分析这条腿原本外接一整套 OpenClaw 网关，为了适配它，WW 背上了约 1500 行集成代码、21 个配置项、一套 WebSocket 设备认证（Ed25519 签名）和 620 行的轮询稳定性状态机——而真正用到的能力只有一句话：收任务、跑一个带工具的 agent、给最终文本。hookprobe 用一个几百行的容器把这件事做完：对外实现 OpenClaw 兼容合同（`POST /hooks/agent` 触发、`GET /sessions/{key}/final` 轮询、isFinal 恒真），WW 改两个环境变量即可切换，零代码改动；引擎是 Claude Agent SDK——agent 循环、内置工具、MCP 客户端、SKILL.md 技能加载全部现成，hookprobe 自己不拥有任何 agent 框架代码。

只读靠三层保证，强度从高到低：真正的边界是挂进容器的只读凭据（只读 kubeconfig、查询型 token）；第二层是 bash 守卫，在工具调用前拦截 kubectl、helm、systemctl、terraform 的变更动词以及 ssh/scp，实测对并行子代理同样生效；第三层是容器本身——非 root、随时可丢弃。失败也有交代：崩溃、超时、被手动停止的运行都会以 isFinal:true 落一份写明 runner 故障的规范报告，调用方一次轮询就能看到，而不是干等超时窗口。

网页操作台 `/ui` 是一个零构建、零外部依赖的单文件页面。左侧是会话列表（状态徽章、模型、轮次、累计费用），右侧逐轮展示对话：JSON 报告自动格式化，Markdown 回答按标题、列表、表格、代码块渲染，超长的告警载荷折叠成一行；每轮下方有账单行——实际运行的模型（含干杂活的辅助模型及其花费）、输入输出 token、缓存读写、费用与耗时。选中已完成的会话，底部输入框就是追问：同一引擎会话续跑，第一轮收集的工具输出、证据、走过的死胡同全部还在。运行中的轮次可以随时 Stop。

![hookprobe sessions 操作台](docs/img/hookprobe-sessions.png)

调查过程全程可见：正在跑的轮次下方实时滚动它的每一步——蓝色是工具调用（带一行摘要），斜体是 agent 在工具之间的自述，计划清单（TodoWrite）渲染成待办列表；跑完后整个过程折叠成 process · N steps，随时点开复盘。下图抓到的是一次调查的开场，也是 skills 闭环的实拍：agent 自己认出了工作目录里沉淀的 runbook，声明要用它，随后逐条执行了 runbook 里的只读命令。调查中验证过的诊断路径会被 agent 沉淀成 SKILL.md，之后的同类告警自动带着这些经验开工——调查员越用越聪明。

![实时过程流：agent 主动调用沉淀的 runbook](docs/img/hookprobe-live-feed.png)

沉淀下来的东西都能在页面里直接管理。skills 视图列出全部 runbook（frontmatter 描述、包含的文件、修改时间），点开即渲染全文；memory 视图编辑环境记忆（workdir 下的 CLAUDE.md），写进去的集群拓扑、已知误报、命名规范会注入每一次调查。实测把「replayd 高 CPU 是本机慢性顽疾」写入记忆后，问它「replayd CPU 很高要不要当事故处理」，它准确回答不需要，并引用了这条背景——环境记忆真的到达了每一轮调查。

![skills 浏览器：沉淀的诊断 runbook](docs/img/hookprobe-skills.png)

![环境记忆编辑器：CLAUDE.md 注入每一次调查](docs/img/hookprobe-memory.png)

## WebhookWise 的位置与切换

WW 保持大而全是明确决策：六路上游适配、渠道渲染、知识库、事件管理、看板都留在它那里，hookjudge 的极简是它的对照组而不是替代品。这次变化只涉及深度分析这一条腿——从 OpenClaw 网关换成 hookprobe，切换只是两个环境变量（合同兼容 + isFinal 恒真，WW 侧零代码改动，原有的稳定性启发式在新合同下自然不再触发）：

```bash
OPENCLAW_ENABLED=true
OPENCLAW_GATEWAY_URL=http://hookprobe:8088
OPENCLAW_HTTP_API_URL=http://hookprobe:8088
OPENCLAW_HOOKS_TOKEN=<同 HOOKPROBE_TOKEN>
```

切换稳定后，WW 可以删掉整段为 OpenClaw 长出来的补偿性复杂度：WebSocket 客户端与设备认证、620 行轮询状态机里的稳定性启发式、websockets 与 cryptography 两个运行时依赖、约一半的 OPENCLAW_* 配置项——这是这次替换真正要兑现的轻量化红利。

## 本地跑起来

管道加判官的演示是自包含的（stub 模型和 sink 都在仓库里），一条命令起全家；调查员需要真实的模型凭据，所以单独起。三个服务各自的 gate 都是 CI 任务的精确本地副本，推送前跑 gate、推送后 CI 复核，是这个仓库的固定纪律。

```bash
# 管道 + 判官（含 stub 模型与 sink）
git clone https://github.com/itswl/hookstack && cd hookstack
docker compose up -d --build      # relay :8100 · judge :8200
bash scripts/stack-smoke.sh       # 或直接跑整套冒烟检查
```

```bash
# 调查员
printf 'HOOKPROBE_TOKEN=change-me\nANTHROPIC_API_KEY=sk-ant-...\n' > .env
docker compose -f hookprobe/deploy/docker-compose.yml up -d --build
open http://127.0.0.1:8088/ui
```

## 现状与下一步

现状：三个服务同仓各自独立，CI 全绿；hookprobe 的每一个能力（合同、续用、过程流、Stop、账单、Markdown、环境记忆、skills 沉淀与浏览、并行子代理、浏览器取证）都经过真实运行验证，本文档的截图即证据。下一步按序推进：给镜像装上告警真实需要的只读 CLI 并搬迁 MCP 配置；部署到服务器与 WW 双跑比对；切换稳定后回 WW 兑现删码；最后给飞书告警卡片加追问按钮直连 `/continue`，让"卡片看结论、追问出细节"成为日常动线。
