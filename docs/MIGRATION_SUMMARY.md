# G:\ck1 录制器项目移植总结

版本：1.0（2026-08-16）
背景：将 G:\ck1 下 4 个录制器项目（openskills-recorder、skill-recorder、
desktop-recorder-skill、ui-regression-recorder-skill）的可借鉴资产移植到 TAGENT。

## 一、移植清单（8 个阶段）

| 阶段 | 能力 | 源项目借鉴 | 落地位置 | 状态 |
| ---- | ---- | ---------- | -------- | ---- |
| 1 | PII 掩码（录制即脱敏） | desktop-recorder 敏感信息处理 | `services/pii_guard.py` + recorder.py | 完成 |
| 2 | 回放评分器（LCS 相似度/成功率/噪音率） | openskills-recorder 自测评估 | `services/eval_scorer.py` + server.py | 完成 |
| 3 | 语义折叠（窗口分段 + 连续输入合并） | openskills-recorder 语义步骤 | compiler.py | 完成 |
| 4 | 定位器置信度（uia/dom/coord/ocr 分级） | skill-recorder 多策略定位 | compiler.py | 完成 |
| 5 | 回放加固（重试 + 事后验证不阻断） | skill-recorder selfheal | player.py | 完成 |
| 6 | 变量参数化（suggest_param 中文关键词） | openskills-recorder 变量注入 | compiler.py | 完成 |
| 7 | CDP 网页注入（不抢焦点，Playwright sync） | desktop-recorder drive-web.js | `services/input/web_channel.py` + player.py | 完成 |
| 8 | 能力注册表（复用/组装/演进） | ui-regression flows.json | `services/capability_registry.py` + routes_tasks.py | 完成 |

## 二、关键实现说明

### 1. PII 掩码（pii_guard.py）
- 规则：邮箱 / 手机号 / 身份证（含 Luhn 加权校验码）/ 银行卡（Luhn）
- 掩码格式 `{{EMAIL}}`、`{{PHONE}}`、`{{ID_CARD}}`、`{{CARD}}`；多处重叠时优先长匹配
- recorder.py 的 `_finalize_text` 接入：密码框（`is_password`）不做掩码，直接 `[密码已保护]`
- 同步写入步骤的 `pii_kinds` 字段供上层识别

### 2. 回放评分器（eval_scorer.py）
- LCS 有序子序列 → `sequence_ratio`；成功步骤占比 → `success_rate`；失败/被跳过 → `noise_ratio`
- intent 命中（可选 keywords）→ 修正评分；综合 verdict：sequence>=0.8 且 success>=0.8 判 pass
- server.py `_attach_eval`：回放报告生成时自动加载 recordings/<rec_id>/events.json 打分

### 3. 语义折叠（compiler.py）
- 窗口切换自动开新「段」（segment，`segment_lead` 标记段首步骤）
- 同一窗口 5 秒内的连续 input_text 合并为单步（`INPUT_MERGE_GAP=5`）

### 4. 定位器置信度（compiler.py `_build_locator`）
- uia(0.95/0.9) > dom(0.88) > coord(0.75/0.6) > ocr(0.55) > screen_coord(0.35)
- 动态 auto_id（含随机数字串）自动过滤，防 session 漂移
- 任务级 `weak_locators` 汇总置信度不足的步骤
- player.py `_sort_locators` 按置信度排序执行

### 5. 回放加固（player.py）
- `LOCATOR_RETRIES=3`、间隔 1.2s；失败后 `_check_locator_retry` 换下一 locator
- `_verify_step` 事后验证（uia/ocr 复核），结果标记 `[verify_ok]`/`[verify_warn]`，不阻断执行

### 6. 变量参数化（compiler.py `_infer_param`）
- `PARAM_KEYWORDS`：搜索/账号/邮箱/手机/验证码等中英文关键词
- 命中时生成 `suggest_param` 字段建议用户标注 `{{var}}`，不自动改写文本

### 7. CDP 网页注入（web_channel.py）
- Playwright sync API `connect_over_cdp` 探测 9222/9223/9224 调试端口（700ms 超时）
- `_find_page` 按窗口标题匹配页面；`maybe_click` 用 dom selector / get_by_text 页内点击
- `maybe_type` / `maybe_key` 仅当 `active`（上次操作走网页）时生效，避免误注入
- player.py 接入：`_do_click`（dom locator 优先 web 通道）、`_do_input_text`/`_do_key_press`（web active 时）
- 全程不抢系统焦点（浏览器场景刚需，规避鼠标被截胡问题）
- 降级：无调试端口/未激活 → 返回 None → 回落到物理输入通道

### 8. 能力注册表（capability_registry.py）
- 将录制的某段步骤区间登记为可复用「能力」（name/source_task/step_range/description）
- 统计复用次数（runs/last_used）；数据存 recordings/capabilities.json（与录制数据同库）
- API：GET/POST /tasks/capabilities、DELETE /tasks/capabilities/{id}、POST .../use
- 注册时校验步骤区间在源任务范围内

## 三、测试与验证

- 单元测试（scripts/run_tests.sh，无外部依赖）：
  test_pii_guard / test_eval_scorer / test_compiler_semantic / test_web_channel
  / test_capability_registry / test_merge_warning —— 全部通过
- E2E 测试（scripts/run_e2e.sh，需后端 18899 + 目标应用）：
  依赖窗口状态（记事本/WPS/计算器），部分环境相关失败属预期
- 手工验证：capabilities API 注册/越界拒绝/删除回路通过

## 四、遗留事项

- [ ] 阶段 2 评分器接入前端展示（后端已就绪）
- [ ] 阶段 7 真实浏览器回放验证（需 Edge/Chrome 以 9222 调试端口启动）
- [ ] 能力注册表前端入口（可先仅 API + 手动注册）
- [ ] 阶段 7 web_click 偶发 element_not_found（回放时机页面状态差异导致，待进一步调查）
- [ ] 浏览器 UI 偏移（UI_OFFSET=110 估算）影响录制侧 elementFromPoint 坐标换算精度，可做自动校准