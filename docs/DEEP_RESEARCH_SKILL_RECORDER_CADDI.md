# Skill Recorder / Caddi 深度研究与 TAGENT 重构建议

研究日期：2026-09-05

研究对象：

- 本地源码：`D:\ck\skill-recorder`
- 公开产品页：<https://www.trycaddi.com/product>
- 技术页：<https://www.trycaddi.com/technology>
- 治理页：<https://www.trycaddi.com/why-caddi>
- Agent 页：<https://www.trycaddi.com/agents>
- Discovery 发布说明：<https://www.trycaddi.com/blog/introducing-caddi-discovery>

## 一、两者真正解决的不是同一个层面

Skill Recorder 是“证据采集和技能生成器”：把屏幕、窗口、URL、剪贴板、语音等事件采集成本地会话，整理成稳定的事件/步骤包，再让 agent 生成可审阅的 intent 和步骤。它的关键不是录制鼠标，而是让**证据、解释、批准后的产物**互相独立。

Caddi 是“面向团队的运营自动化产品”：Discovery 找出重复工作，Loop Studio 让用户像培训新员工一样示范，Automation Plan 生成混合 agent，Open Questions 暴露未知分支，Run History 和治理页面让管理者知道谁做了什么、触碰了哪些系统、节省了多少时间。

共同核心可以抽象成：

```text
发现/采集证据 -> 解释用户意图 -> 生成可审阅计划 -> 确定性运行
                                      |                 |
                                 开放问题/人工确认       运行审计
                                      |                 |
                              运行后提出变更 -> 审批后发布新版本
```

## 二、Skill Recorder 的可验证工程做法

### 1. 事件不是步骤：使用 append-only typed event stream

`common/types.ts` 定义了带 `seq、t、epoch、type、source、payload` 的 `RecEvent`；`common/events.ts` 进一步约束每种事件的 payload。`SessionStore` 将事件追加到 `events.jsonl`，事件流失败会被捕获，不让未处理的 stream error 直接杀死主进程。

这解决了 TAGENT 当前的两个问题：

- `events.json` 是一个整体覆盖写文件，录制中途崩溃容易留下不完整产物。
- 鼠标/键盘事件在 `Recorder._process` 中直接折叠成业务步骤，之后很难判断某个步骤是事实、推断还是清理结果。

### 2. EventBus + CollectorHost 隔离采集器

`electron/recorder/event-bus.ts` 负责持久化和广播；`collector.ts` 负责启动/停止多个采集器，并隔离单个采集器失败。窗口、剪贴板、URL、视频等来源可以独立启停。

TAGENT 应把低级钩子、窗口采集、截图和剪贴板视为独立 collector；任何一个 collector 失败，都应在会话元数据中记录 `degraded`，而不是让编译器猜测“没有事件就是用户没有操作”。

### 3. 会话控制器串行化所有生命周期变化

`RecorderController` 用 `enqueue()` 串行化 start/stop/discard/麦克风切换/关闭应用，状态包括 `transition=starting|stopping|discarding`。停止时先停 collector，再结束 event stream，最后异步做视频帧处理；后处理失败不会把 UI 卡在 stopping。

TAGENT 目前虽然有 `_recorder_lock`，但录制、回放、重新演示和关闭之间仍依赖多个全局变量。应引入单一 `SessionController`，把 `idle -> starting -> recording -> stopping -> ready|failed` 作为唯一状态源。

### 4. 确定性 bundle 与 agent analysis 分离

`common/bundle.ts` 的 `SessionBundle` 是事件/帧整理结果；`common/analysis.ts` 是 agent 输出，带 `intent、steps、evidence、confidence、feedbackLog、approved、revision`。只有用户批准 analysis 后，才进入 skill/automation builder。

TAGENT 的 `task.json` 目前既是 compiler 输出，又是用户修改变量/纠错的存储，又是 player 的执行输入。应拆成：

- `events.jsonl`：事实记录，只追加。
- `bundle.json`：确定性整理结果，可重建。
- `plan.json`：可运行计划，带版本和安全等级。
- `review.json`：用户反馈、开放问题、批准状态。
- `runs/<run_id>.json`：每次执行的审计结果。

### 5. 评测不是只测“接口成功”

Skill Recorder 的 evals 将 describer、automation builder、skill builder 和 sensitive redaction 分开评分；rubric 会明确检查意图、步骤数量、应用顺序、必须提到的实体以及禁止出现的录制噪声。builder eval 还会强制 native tool 优先，避免 agent 退回浏览器点击。

TAGENT 应把以下指标拆开：

- 证据完整率：事件是否有 seq/source/timestamp。
- 计划正确率：步骤顺序、窗口/DOM 定位和变量引用。
- 执行可靠率：每一步真正成功，而非仅 HTTP 200。
- 安全准确率：destructive/敏感信息是否正确拦截。
- 泛化率：换窗口标题、分辨率和输入值后是否仍能执行。

## 三、Caddi 的产品/运行哲学

以下结论来自公开页面的明确产品描述；具体内部实现属于推断，不应当当作源码事实。

### 1. Discovery 先于 Automation

Discovery 以只读方式读取用户已经连接的工具，通常在 15–30 分钟内确认重复模式，按每年可节省的小时数排名，并给出任务、证据、置信度和 ROI，再一键交给 Studio 构建。

TAGENT 现在的 Scout 只是“任务数 + 步骤数”摘要，不是真正的发现能力。第一版不需要云端遥测，可以从本地 recordings/runs 计算：

- 同类流程出现次数
- 平均步骤数和耗时
- 成功率/失败率
- 人工接管率
- 估算可节省时间

### 2. Teach 与 Plan 分离

Caddi 的 Loop Studio 有明确的阶段反馈：“我看到了每一步” -> “已构建，按 Run 执行” -> “运行中，观察你的工具”。录制只是示范，最终执行的是审阅后的 Automation Plan。

TAGENT 应避免停止录制后立即把原始录制当作正式任务运行。至少应在任务卡增加一个轻量 plan 预览：目标、步骤、定位证据、变量、风险和开放问题；没有批准时仍可允许本地试跑，但要明确标成 draft。

### 3. Hybrid agent：确定性 80%，开放问题才交给 agent

Caddi 技术页明确强调“Deterministic steps, bounded AI”：API/代码负责稳定步骤，AI 只处理真正需要判断的地方；Why Caddi 页面又明确说“improves after runs, not during them”，也就是运行中不让模型随意改流程。

TAGENT 当前已经有 UIA -> DOM -> 坐标 -> OCR 的定位链，但仍把失败步骤直接带入人工接管。应把每一步显式标注：

- `deterministic`：定位证据足够，按固定动作执行。
- `agent_assist`：只生成候选修复，不直接执行。
- `human_required`：登录、2FA、destructive 或开放问题，必须暂停。

### 4. Open Questions 是正式产物，不是错误日志

Caddi 会问“Where does the executed copy get filed?”、“What if only one party has signed?”，并允许用户用自然语言回答；规则得到确认后才加入流程。

TAGENT 应在 compiler 输出 `open_questions[]`，至少覆盖：

- 动态窗口无法稳定匹配
- 只有屏幕坐标、没有 UIA/DOM 证据
- 新窗口/对话框由前一步创建但没有验证
- destructive 操作的目标或确认条件不清楚
- 录制中存在跨应用但没有明确数据传递

### 5. Run History 和治理是核心功能

Caddi 的每次运行都记录谁运行、触碰什么、产生什么；管理者能暂停、放行或审计每个 agent。技术页还强调 OAuth/SSO 下使用用户自身权限、所有 API/action 可导出审计。

TAGENT 已经生成 `runs/*.json`，但 UI 尚未把它作为一等对象。建议每次 run 固定记录：

```json
{
  "run_id": "...",
  "task_id": "...",
  "plan_revision": 3,
  "started_at": "...",
  "finished_at": "...",
  "trigger": "manual|schedule",
  "steps": [{"index": 0, "status": "ok", "method": "post_message", "evidence": []}],
  "human_takeovers": [],
  "side_effects": [],
  "summary": {"duration_ms": 0, "success": true}
}
```

### 6. “角色 Agent”不是通用聊天机器人

Caddi Agents 页面把 Agent 定义为一个真实后台岗位的高频、跨系统、确定性 80%，例如 AP、AR、Document Processor、Intake Specialist，而不是一个会自由发挥的聊天窗口。

TAGENT 的能力注册表可以沿这个方向演进：能力应包含输入、前置条件、输出、副作用、可复用步骤范围和成功样本，而不仅是 `source_task + step_range`。

## 四、TAGENT 当前结构性差距

| 领域 | TAGENT 当前状态 | 直接后果 | 优先修复 |
| --- | --- | --- | --- |
| 录制事实 | `events.json` 整体写入，事件/步骤混合 | 自身 UI、噪声和业务动作容易混在一起 | typed append-only event log |
| 计划 | `task.json` 同时充当计划和运行输入 | 重编译会覆盖用户变量/纠错，旧任务难迁移 | plan revision + immutable source |
| 生命周期 | 全局 recorder/player + 多把锁 | stop/replay/rerecord 竞态 | 单一 session/replay controller |
| 定位 | UIA、DOM、坐标、OCR 链混在 player | 失败原因不清，容易永久等待 | 每步 locator evidence + bounded retry |
| 开放问题 | 只有“暂停/报错” | 用户不知道需要回答什么 | `open_questions[]` + answer/approve |
| 治理 | REPORT 有数据，UI 不可审计 | 无法知道谁/何时/为什么改变流程 | Run History + plan revisions |
| 发现 | Scout 只有数量统计 | 不知道先自动化哪个流程 | 本地重复模式/ROI 统计 |
| 评测 | 真实 E2E 与 compiler eval 未完全分离 | 通过 HTTP 不能证明 UI/动作正确 | fixture + runtime + visual acceptance |

## 五、建议的重构顺序

### R0：先稳定执行边界（必须做）

1. 保留当前 `task.json` 兼容读取，但引入 `plan_revision` 和 `source_hash`。
2. 回放前生成不可变的 `RunContext`，不再回放中隐式重编译覆盖用户编辑。
3. 每个动作有硬超时、重试预算和最终状态；不允许无界等待。
4. `open_questions` 未解决时明确显示等待原因；Stop 必须始终可用。

### R1：拆分 Evidence / Plan / Run

1. 录制写 `events.jsonl`，每条事件有 `seq/source/timestamp`。
2. compiler 产出 `bundle.json` 与 `plan.json`，plan 带 `deterministic|agent_assist|human_required`。
3. corrections 变成 reviewed patch：试用次数、成功率、批准版本和回滚入口。

### R2：把 Caddi 的产品闭环做成轻量本地版

1. Scout：本地重复工作与 ROI。
2. Teach：录制后显示“已看到的步骤”和证据。
3. Plan：用户确认目标、变量、开放问题和副作用。
4. Run：确定性执行 + 人工接管。
5. History：按任务/版本查看每次运行。

### R3：再接入 bounded agent

只有 R0–R2 稳定后，才允许 LLM 生成 locator/correction/open-question 建议；建议必须隔离试用，人工批准后才进入正式 plan。运行中的 agent 不得自行改写 plan。

## 六、明确不应直接复制的部分

- 不复制 Caddi 品牌、原始文案、视觉素材或其云端架构。
- 不把 OAuth/SSO、75+ SaaS 集成、云端遥测作为 TAGENT V1 前置条件。
- 不引入 Skill Recorder 的 Electron/Copilot/Whisper 全套依赖；只借鉴其数据契约、状态机、隐私和评测边界。
- 不把“AI agent”当成回避确定性工程的理由；桌面自动化仍然要先验证目标、边界和副作用。

