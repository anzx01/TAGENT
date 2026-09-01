# Caddi 产品对标与 TAGENT 改进建议

阅读来源：<https://www.trycaddi.com/>、<https://www.trycaddi.com/product>（2026-09-02）。Caddi 的核心定位是“发现重复工作 -> 像培训新员工一样示范 -> 生成混合 agent -> 治理和审计”，产品页明确展示 Integrations、Discovery、Loop Studio、Automation Plan、Open Questions、Run History、Scout 等模块。

## 值得借鉴

1. **从发现问题开始**：不要求用户先定义自动化脚本，而是先找出跨团队的重复工作。TAGENT 可增加“候选流程”视图：按重复次数、耗时、失败率排序，帮助用户决定先录什么。
2. **录制与意图分离**：Caddi 将“screenshare and explain”与后续 Automation Plan 分开。TAGENT 应保留原始证据，同时允许用户用一句话修正目标，再编译 task，避免把偶然点击固化成规则。
3. **混合执行模型**：确定性步骤负责稳定动作，agent 只处理开放问题和边界情况。TAGENT 当前已有 locator 降级链，可进一步为每一步标注 `deterministic` 或 `agent_assist`，并默认关闭后者。
4. **明确暴露未知项**：Open Questions & Edge Cases 是产品的一等输出。TAGENT 应在编译结果中生成 `open_questions[]`，遇到未覆盖分支时暂停并询问，而不是猜测继续。
5. **治理即产品功能**：Run History 强调每次决策和节省时间可审计。TAGENT 的 REPORT 应增加输入摘要、每步证据、人工接管、变量来源、耗时和结果，并提供按任务/时间筛选。
6. **面向管理者的价值视图**：Scout 关注团队级工作分布。TAGENT 可在不上传原始截图的前提下统计任务次数、平均耗时、成功率和人工介入率。

## 不宜照搬

- Caddi 面向专业服务团队和 80+ SaaS 集成；TAGENT 的 Windows 桌面跨应用定位仍应保持本地优先。
- 不把“agentic”作为默认执行器；高风险动作继续 fail-closed，destructive 操作必须人工确认。
- 不为管理看板引入重量级云端平台，先用本地 JSON/SQLite 产出可导出的统计。

## TAGENT 下一版验收标准

- 每个编译任务都能展示：目标、确定性步骤、agent 辅助步骤、开放问题和安全等级。
- 回放遇到开放问题时进入 `waiting_for_human`，可继续、跳过或重新示范该步骤。
- REPORT 可回答：做了什么、为何这样做、哪一步由人接管、耗时多少、是否产生副作用。
- 统计页只使用脱敏元数据，不读取或上传原始截图/剪贴板内容。

