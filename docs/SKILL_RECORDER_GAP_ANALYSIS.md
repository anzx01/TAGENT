# TAGENT 与 Skill Recorder 取长补短清单

参考基线：`D:\ck\skill-recorder`（本地源码，未复制其实现或依赖）。TAGENT 当前已经具备录制、回放、定位降级、PII 文本掩码、能力注册和验收测试；本清单聚焦仍值得补强的工程能力。

## 已经吸收的优点

- 录制 -> 分析/编译 -> 执行 -> 人工接管/自愈的闭环。
- 本地优先的数据存储和可回放的 session/task 产物。
- 敏感信息扫描与掩码，以及 destructive 操作的安全闸门。
- 通过 fixture/E2E 验收，而不是只验证 HTTP 接口返回值。

## 建议优先补强（按收益排序）

### 1. 录制前隐私确认与“实际采集内容”预览

Skill Recorder 在每次录制前明确展示窗口、URL、剪贴板、截图和语音等采集范围，并在分析上传前再次确认。TAGENT 目前主要依赖后台 PII 过滤，用户不容易知道哪些内容会落盘。

建议：

- 在 `record` 前展示一次性确认面板，列出当前启用的采集器和禁止录入的内容。
- 在 `stop` 后提供“本次实际采集”摘要（窗口标题、URL、剪贴板命中数、截图数）。
- 把 `redacted / blocked / user-approved` 写入 recording 元数据，便于审计。

### 2. 敏感信息从文本扩展到截图/OCR

`python/services/pii_guard.py` 已覆盖文本中的邮箱、手机号、身份证和银行卡号；Skill Recorder 还对截图做 OCR、敏感词/模式扫描并支持帧级遮挡。

建议：

- 录制落盘前对关键帧执行 OCR 扫描；命中时对矩形区域模糊或纯色遮挡。
- 增加“整帧丢弃”策略，处理无法可靠定位的密码/Token 场景。
- 将 OCR 掩码结果加入测试 fixture，验证中文、英文和低分辨率截图。

### 3. 明确的会话状态机与崩溃恢复

参考 `electron/recorder/controller.ts`、`sessions.ts` 和 crash guards，录制状态应有严格的 `idle -> recording -> stopping -> ready -> analyzing -> complete/failed` 转移，异常退出后可恢复最近 checkpoint。

建议：

- 为 recorder/player 增加非法状态转移保护和幂等 `stop`。
- 每个 session 持久化 `state、heartbeat、last_event、checkpoint`。
- 启动时将未完成会话标记为 `interrupted`，允许从最后安全步骤继续。

### 4. 把“编译质量”变成可回归的评测集

Skill Recorder 的 `evals/` 将描述器和 builder 分开评分，并使用固定场景、干扰项和通用性指标。TAGENT 当前验收脚本覆盖真实流程，但缺少针对编译结果的离线回归基线。

建议：

- 增加 `python/tests/fixtures/recordings/`，覆盖浏览器、Office、跨窗口、变量和等待步骤。
- 分别评估：意图准确率、步骤顺序、定位器可执行率、变量泛化率和安全分类准确率。
- 每次 compiler/locator 修改运行快速评分；低于基线时阻止发布。

### 5. 诊断包与 Windows 发布验证

参考 `doctor.ts`、`debug-bundle.ts`、`verify-windows-package.mjs` 和合规脚本，发布前应能自检 sidecar、权限、依赖、架构和数据目录，并生成可脱敏诊断包。

建议：

- 增加 `tagent doctor`（或等价脚本）：检查 Python、Tesseract、窗口钩子、端口、录制目录和 sidecar 健康状态。
- 生成只含版本、状态和错误摘要的 debug bundle，默认排除截图、剪贴板和凭据。
- 在 Win10/Win11、x64/ARM64 上执行安装包 smoke test，并验证卸载不删除用户 recordings。

## 不建议照搬

- 不引入 Copilot CLI、Electron 或 Whisper 作为 TAGENT 的强制依赖；现有 Tauri + Python sidecar 与 Windows 原生定位更贴合。
- 不把云端分析设为默认路径。TAGENT 应保持本地编译/回放优先，只有用户主动开启时才上传脱敏数据。
- 不复制其完整 UI 和多媒体流水线；V1 继续围绕“录制、回放、可控自愈”保持轻量。

## 推荐落地顺序

1. 录制前确认 + 实际采集摘要（低风险、立刻改善信任）。
2. 会话状态机/checkpoint（降低崩溃和重复录制成本）。
3. 截图 OCR 遮挡（补齐隐私边界）。
4. compiler/locator fixture eval（建立质量门槛）。
5. doctor/debug bundle/Windows package 验证（发布稳定性）。

## 本轮整改状态

- [x] 会话状态与心跳元数据：`python/recorder.py` 已在 start/stop 阶段原子更新 `meta.json`。
- [x] 基础 Windows 诊断：新增 `python/doctor.py`，检查 Python、录制目录、Tesseract、Win32/UI 依赖及中断会话。
- [ ] 录制前确认面板与截图 OCR 遮挡（下一轮 UI/图像处理改造）。
- [ ] compiler/locator fixture 评测门槛（下一轮测试基线改造）。
