# Agent 编排自研，底层能力用 LangChain

多 Agent 系统（Orchestrator / Scout / Planner / Navigator / Storyteller）的编排层——Agent Loop、工具系统、Agent 间消息传递——自行实现；模型调用、embedding、结构化输出等底层能力通过 LangChain 接入。

**为什么**：项目是面试作品集，自研编排能把 ReAct 循环、工具路由、多 Agent 协作逐层讲透（与 hello-agents 教程一脉相承）；LangChain 只作可替换的底层适配，避免编排逻辑被框架黑盒吞掉。

**Considered Options**：LangGraph 全家桶（生产口碑好，但编排逻辑变成"会调库"，面试讲不深，拒）。

**Consequences**：需要自己实现 tracing/观测钩子，评测 harness 也依赖这些钩子收集 Agent 行为数据。
