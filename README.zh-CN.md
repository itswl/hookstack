# hookstack

[English](README.md) · **中文**

**让 agent 值班，但不把钥匙交出去。**

一条信号进来：告警、聊天消息、工单、定时任务。管道给它签名、路由、计价。agent 只读地调查它，并在启动时证明自己是只读的。报告作为一张卡片到人手里，人可以直接裁定。没有一次签名的按下，什么都碰不到生产。

| 服务 | 做什么 |
| --- | --- |
| [`hookrelay`](hookrelay) | **管道。** 接任意 webhook，路由，投递到聊天，每一跳都记进同一本账。 |
| [`hookjudge`](hookjudge) | **判官。** 便宜地判断是否需要有人马上处理。大多数事件用不着调模型。 |
| [`hookprobe`](hookprobe) | **调查员。** 每个事件跑一次只读的 agent 调查，对外是 OpenClaw 兼容的 HTTP 契约。也能单独使用。 |

调查员是产品本身，管道和判官是给没有告警平台的团队准备的最小前端。一套部署就是一个团队：它不是工作管理平台，不是多租户 SaaS，也不是 agent 框架。

## 从这里开始

- **30 秒：**[docs/quickstart.md](docs/quickstart.md) 开头就讲清了它的形状 —— 三个服务，一张图。
- **10 分钟，不要 key，不花钱：** 管道 + 判官 + 桩模型 + 跑排练的调查员 + 可读的接收端，只要有 Docker。

  ```bash
  curl -fsSLO https://raw.githubusercontent.com/itswl/hookstack/main/docker-compose.quickstart.yml
  docker compose -f docker-compose.quickstart.yml up -d
  bash <(curl -fsSL https://raw.githubusercontent.com/itswl/hookstack/main/scripts/demo.sh)
  ```

  演示会跑完整条闭环：告警被判定，一次录好的调查经过真实的只读门回放，报告变成卡片，按下批准后跑两条允许清单里的命令，告警恢复。[docs/quickstart.md](docs/quickstart.md)
  里有其余内容 —— 换成真实模型、三块看板、半天的路径。
- **半天，真上：**[STACK.md](STACK.md) 从源码一步步跑起三个服务；[docs/containment.md](docs/containment.md) 是指向真实集群**之前**要读的边界地图。
- **完整功能清单：**[docs/what-you-get.md](docs/what-you-get.md)（英文）。

![hookprobe 的控制台：一次真实的调查，结论在最前面，查不到的根因明说查不到，下面是这次的花费](docs/img/zh/hookprobe-sessions.png)

三块看板都能切换中英文，并跟随系统的浅色或深色；[完整介绍](OVERVIEW.md)（英文）里有全部截图，取自本地真实运行，不是效果图。

## 怎么串起来

```
上游 ──► hookrelay ──► hookjudge ──► hookrelay ──► 聊天桥 / webhook
             │
             └──► hookprobe ──► hookrelay ──► 同样的通道
                  （critical/high；没有模型时跑排练）
```

同一条管道也搬运操作者自己的工作信号，交给规划器，再经人按下交接后由执行节点去做（[两种部署形状，一份代码](docs/deployments.md)）。

## 延伸阅读

以下文档都是英文。

- [docs/quickstart.md](docs/quickstart.md)：应该从这里读的第一页
- [docs/what-you-get.md](docs/what-you-get.md)：完整功能清单
- [OVERVIEW.md](OVERVIEW.md)：完整介绍，含全部截图
- [STACK.md](STACK.md)：在本地一步步跑起整个栈
- [hookrelay](hookrelay/README.md)、[hookjudge](hookjudge/README.md)、[hookprobe](hookprobe/README.md)：每个服务的详细说明
- [docs/containment.md](docs/containment.md)：安全边界
- [docs/bridge-protocol.md](docs/bridge-protocol.md)：卡片怎么送到聊天平台
- [CONTRIBUTING.md](CONTRIBUTING.md)：gate 与开发流程

## 开发

`bash scripts/gate.sh` 会跑每个服务的 gate 和整个栈的检查。提交前读它的结论。管道的源码上限是 6,400 行，判官 3,400 行，调查员不设上限。

MIT 协议。
