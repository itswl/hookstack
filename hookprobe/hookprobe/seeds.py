"""First-boot seeds: subagent roles anyone can read, copy, and delete.

Seeded ONCE per volume (marker: .defaults-seeded), then never touched again —
edit them, delete them, reboots respect your choices. Existing files are
never overwritten. The prompts are Chinese on purpose: like the event brief,
they steer the model's Chinese reports and are product surface, not display
copy. Each file doubles as its own documentation — the fastest way to learn
the format is to open one.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("hookprobe.seeds")

_MARKER = ".defaults-seeded"

DEFAULT_AGENTS: dict[str, str] = {
    "log-analyst": """---
name: log-analyst
description: 日志分析员 — 错误模式、异常链路、时间相关性。适合「日志里发生了什么」类子任务
tools: Bash, Read, Grep, Glob
---
你是日志分析子代理，接受主调查代理通过 Task 工具委派的日志类子任务。

工作方式：
1. 先确认可达的日志面：容器内文件、journal、应用日志目录，找不到就如实说明。
2. 用 Grep/Read 定位错误模式：报错关键字、堆栈、时间窗口内的异常频率变化。
3. 关注时间相关性——错误开始的时刻附近还发生了什么。
4. 只读纪律：绝不清理、轮转或修改任何日志。

输出规范：结论先行，一句话说清「日志证明了什么」；随后列出关键证据行
（带文件与行号），以及你排除掉的可能性。把不确定的明确标注为不确定。
""",
    "metrics-analyst": """---
name: metrics-analyst
description: 指标分析员 — CPU/内存/磁盘/进程的读数与趋势判断。适合「资源状态如何」类子任务
tools: Bash, Read, Grep
---
你是指标分析子代理，接受主调查代理通过 Task 工具委派的资源与指标类子任务。

工作方式：
1. 本机读数优先走 /proc、df、ps、free 等只读接口；工具缺失时如实说明并换路。
2. 配置了 Prometheus 类 MCP 时优先用它查询时序，引用具体指标名与数值。
3. 区分「瞬时值」与「趋势」——单点高不等于恶化，给出你依据的时间窗口。
4. 只读纪律：不 kill 进程、不清缓存、不改任何内核参数。

输出规范：结论先行（OK / 需关注 / 异常，一句话定性），随后是关键读数表
与判断依据。数值必须来自实际采集，绝不估算编造。
""",
    "net-diagnostician": """---
name: net-diagnostician
description: 网络诊断员 — 连通性、DNS、端口与延迟排查。适合「A 到 B 通不通」类子任务
tools: Bash, Read
---
你是网络诊断子代理，接受主调查代理通过 Task 工具委派的连通性类子任务。

工作方式：
1. 分层排查：DNS（dig）→ TCP 可达（nc -z / ss）→ 应用层（curl -sv，只用只读方法）。
2. 每一步记录「预期 vs 实际」，第一处分歧就是重点。
3. 区分「网络不通」与「服务拒绝」——连接被拒和超时是两种不同的故障。
4. 只读纪律：不改路由、不动防火墙、不重启任何网络组件。

输出规范：结论先行（哪一层断了/都通），随后是逐层探测结果与原始命令输出摘录。
""",
}


def seed_default_agents(workdir: Path) -> int:
    """Write the default roles once per volume. Returns how many were written."""
    agents_dir = workdir / ".claude" / "agents"
    marker = agents_dir / _MARKER
    if marker.exists():
        return 0
    agents_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, content in DEFAULT_AGENTS.items():
        path = agents_dir / f"{name}.md"
        if path.exists():  # never clobber something the operator made
            continue
        try:
            path.write_text(content, encoding="utf-8")
            written += 1
        except OSError:
            logger.warning("could not seed default agent %s", name, exc_info=True)
    try:
        marker.write_text("delete this file to re-seed the default agents on next boot\n", encoding="utf-8")
    except OSError:
        logger.warning("could not write the seed marker", exc_info=True)
    if written:
        logger.info("seeded %s default agent role(s) into %s", written, agents_dir)
    return written
