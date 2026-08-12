# hook 系列告警家族总览

一组围绕"告警怎么被处理"生长出来的小服务。家族的分工哲学是一件事一个组件：**hookrelay** 是管道，把各家监控的方言适配进来、把结果按各渠道的格式送出去；**hookjudge** 是判官，一条事件、一个裁决、一笔账；**hookprobe** 是调查员，对值得深挖的告警做一次带工具的只读 agent 调查。三个服务同住本仓库，各自完全独立（自己的包、测试、gate、Dockerfile、CI），拼在一起是一条完整的告警处理流水线。

文中所有截图都来自 2026-08-12 真实运行的服务与真实数据，不是设计稿；管道与判官两张摄自无历史数据的本机 Docker 全新环境，与 [STACK.md](STACK.md) 的验证步骤一一对应。

```
上游告警源(Grafana / Alertmanager / 云监控 …)
      │
      ▼
  hookrelay :8100 ──► hookjudge :8200 ──► hookrelay ──► 飞书 / 钉钉 / 企微
  (管道:适配+路由+账本) │  (判官:裁决+成本)     (格式化投递)
                      │
                      └──► hookprobe :8088 ──► hookrelay ──► 同样的渠道
                           (调查员:只读深度调查)  /hook/probe-notify
```

## 家族分工

| 组件 | 角色 | 一句话职责 | 刻意不做 |
| --- | --- | --- | --- |
| [`hookrelay/`](hookrelay) | 管道 | 把每种上游方言适配成统一事件，按路由送去大脑，再把裁决与报告渲染成各渠道格式投递，全程记账 | 不理解内容，不做判断 |
| [`hookjudge/`](hookjudge) | 判官 | 一进（事件）一出（裁决），四条路由按成本排序：recovery、reuse、ai、rule | 不渲染卡片，不管渠道 |
| [`hookprobe/`](hookprobe) | 调查员 | 对重要告警跑一次带工具的只读 agent 调查，交回根因报告；会话可追问，经验会沉淀 | 不接告警，不发通知 |

拆分的道理在于：一个要渲染飞书卡片的大脑，就得懂飞书的卡片 schema，然后是企微的、钉钉的——这些活属于管道。把它们挪进管道，大脑才可以被替换、被比较，而两端不动；hookjudge 刻意做成能撑起这份契约的最小大脑。判官和调查员回答的也是两个不同的问题：判官答"这条告警值不值得打扰人"，调查员答"到底发生了什么"——所以裁决秒级先到，深度报告几分钟后跟进，落在同一个渠道里。

## hookrelay：管道

管道是唯一的告警前门。上游方言在配置里声明式适配：占位符从原始载荷抽取标题、正文、级别，level_map 把 alerting、firing 这类各家用词翻译成统一级别；路由表决定事件去哪——默认一切进大脑，重要事件同时复制一份给调查员，而判官与调查员的回传走更高优先级的路由直达渠道并就此停住，防止结果被再次送去加工。每条消息有交代：排队、送达、死信在账本页一眼可见，每个事件都能点开查看完整的决策链。

下图是在干净的本机 Docker 环境（`docker compose down -v` 后全新拉起）打完四发演示告警后的账本：每个前门事件一次决策同时路由到 to-judge 与 to-probe（升级复制），判官回传的 judge-notify 被路由到 ops-feishu 和 ops-dingtalk 两个渠道——16 次投递全部送达、零排队、零死信。

![hookrelay 账本页：每条消息有交代，每次投递有结局](docs/img/hookrelay-ledger.png)

## hookjudge：判官

判官只做一件事：收一条标准化事件，给一个裁决（重要性、分类、一句话摘要），回传给管道，同时记一笔账。它的成本策略直接写在路由顺序里：**recovery**（恢复事件继承其告警的裁决，免费）→ **reuse**（同一状况的复述沿用上一次 AI 裁决，免费）→ **ai**（真正花钱问模型）→ **rule**（规则兜底）。省钱的关键不是把模型调便宜，而是让大多数事件根本走不到 ai 这一步。

下图是那四发演示告警的裁决账本：支付网关 5xx 第一次出现走 ai 付费，同一状况的复述命中 reuse 免费；磁盘告警走 ai 付费，随后的恢复走 recovery 免费——4 条裁决、付费比例 50%、总花费 $0.000524、回传失败 0。这套数字和 [STACK.md](STACK.md) 里承诺的演示结果完全一致。

![hookjudge 状态页：四条裁决与各自路由，付费比例 50%](docs/img/hookjudge-status.png)

## hookprobe：调查员

有些告警值得的不只是一个裁决，而是一次真正的排查。通常这意味着外接一整套 agent 网关产品，为了"收任务、跑一个带工具的 agent、拿回最终文本"这一件事，背上渠道、设备配对、会话产品的全部行李。hookprobe 用一个几百行的容器把这件事做完：对外暴露 OpenClaw 兼容的触发/轮询合同（`POST /hooks/agent`、`GET /sessions/{key}/final`、isFinal 恒真），已经按那套方言集成过网关的调用方改一个 URL 即可切换；引擎是 Claude Agent SDK——agent 循环、内置工具、MCP 客户端、SKILL.md 技能加载全部现成，hookprobe 自己不拥有任何 agent 框架代码。

只读靠三层保证，强度从高到低：真正的边界是挂进容器的只读凭据（只读 kubeconfig、查询型 token）；第二层是 bash 守卫，在工具调用前拦截 kubectl、helm、systemctl、terraform 的变更动词以及 ssh/scp，实测对并行子代理同样生效；第三层是容器本身——非 root、随时可丢弃。失败也有交代：崩溃、超时、被手动停止的运行都会以 isFinal:true 落一份写明 runner 故障的规范报告，调用方一次轮询就能看到，而不是干等超时窗口。

网页操作台 `/ui` 是一个零构建、零外部依赖的单文件页面。左侧是会话列表（状态徽章、模型、轮次、累计费用），右侧逐轮展示对话：JSON 报告自动格式化，Markdown 回答按标题、列表、表格、代码块渲染，超长的告警载荷折叠成一行；每轮下方有账单行——实际运行的模型（含干杂活的辅助模型及其花费）、输入输出 token、缓存读写、费用与耗时。选中已完成的会话，底部输入框就是追问：同一引擎会话续跑，第一轮收集的工具输出、证据、走过的死胡同全部还在。运行中的轮次可以随时 Stop。

![hookprobe sessions 操作台](docs/img/hookprobe-sessions.png)

调查过程全程可见：正在跑的轮次下方实时滚动它的每一步——蓝色是工具调用（带一行摘要），斜体是 agent 在工具之间的自述，计划清单（TodoWrite）渲染成待办列表；跑完后整个过程折叠成 process · N steps，随时点开复盘。下图抓到的是一次调查的开场，也是 skills 闭环的实拍：agent 自己认出了工作目录里沉淀的 runbook，声明要用它，随后逐条执行了 runbook 里的只读命令。调查中验证过的诊断路径会被 agent 沉淀成 SKILL.md，之后的同类告警自动带着这些经验开工——调查员越用越聪明。

![实时过程流：agent 主动调用沉淀的 runbook](docs/img/hookprobe-live-feed.png)

沉淀下来的东西都能在页面里直接管理。skills 视图列出全部 runbook（frontmatter 描述、包含的文件、修改时间），点开即渲染全文；memory 视图编辑环境记忆（workdir 下的 CLAUDE.md），写进去的集群拓扑、已知误报、命名规范会注入每一次调查。实测把「replayd 高 CPU 是本机慢性顽疾」写入记忆后，问它「replayd CPU 很高要不要当事故处理」，它准确回答不需要，并引用了这条背景——环境记忆真的到达了每一轮调查。

![skills 浏览器：沉淀的诊断 runbook](docs/img/hookprobe-skills.png)

![环境记忆编辑器：CLAUDE.md 注入每一次调查](docs/img/hookprobe-memory.png)

## 家族闭环：升级进，报告出

调查员接在家族自己的告警流里：管道的升级路由把每个前门事件复制一份到 `/hooks/event`，是否值得花钱调查由 probe 按级别自决（默认 critical/high，按 source+event_id 幂等——告警风暴只养一次调查）；调查完成后报告回传管道的 `probe-notify` 前门（家族统一的时间戳 HMAC 签名），由管道打扮成卡片送进和裁决相同的渠道。管道保持内容盲，判官一行未动，失败的调查也会以报告形式走完闭环。

默认演示 compose 把升级投递指向 sink 的替身路径 `/probe-standin`——无需模型凭据就能看到升级的形状，冒烟检查会验证每个前门事件都被复制到了替身。`docker compose --profile probe up` 换成真调查员。首次实测闭环：一发「宿主机 CPU 持续高负载」告警，判官一秒内给出裁决，3.7 分钟后调查报告落到同一渠道——判定为误报，且点名了唯一可处置项（一条失控的 log stream 过滤器把 diagnosticd 推高了 0.75 核）。

## 本地跑起来

管道加判官的演示是自包含的（stub 模型和 sink 都在仓库里），一条命令起全家；调查员需要真实的模型凭据，所以是可选档位。三个服务各自的 gate 都是 CI 任务的精确本地副本，推送前跑 gate、推送后 CI 复核，是这个仓库的固定纪律。

```bash
# 管道 + 判官（含 stub 模型与 sink）
git clone https://github.com/itswl/hookstack && cd hookstack
docker compose up -d --build      # relay :8100 · judge :8200
bash scripts/stack-smoke.sh       # 或直接跑整套冒烟检查
```

```bash
# 加入调查员（需要真实模型凭据）
printf 'ANTHROPIC_API_KEY=sk-ant-...\nHOOKPROBE_EVENT_URL=http://hookprobe:8088/hooks/event\n' >> .env
docker compose --profile probe up -d --build
open http://127.0.0.1:8088/ui
```

## 现状与下一步

现状：三个服务同仓各自独立，CI 全绿；调查员的每一个能力（合同、续用、过程流、Stop、账单、Markdown、环境记忆、skills 沉淀与浏览、并行子代理、浏览器取证、家族闭环）都经过真实运行验证，本文档的截图即证据。下一步按序推进：给镜像装上告警真实需要的只读 CLI 并搬迁 MCP 配置；部署到生产环境接真实告警流；把升级级别、费用预算按实际噪声水位调准。
