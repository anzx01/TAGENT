# TAGENT

**演示一次,替你重复。** 本机优先的 Windows 桌面自动化助手:录下你在真实应用里的操作,编译成可审计的任务,之后一键回放、定时执行、条件触发、失败找你。

## 核心理念

- **先录屏再操作**:一切任务来自真实演示,不凭空生成步骤。
- **确定性优先**:回放按录制的定位链(UIA → 窗口百分比 → 屏幕坐标)逐步执行;LLM 只做辅助(自愈建议、意图润色),永不改变执行中的步骤。
- **人在环上**:破坏性操作确认、会话步骤(登录/2FA)暂停等待人工、失败后显式 `CALL_USER` 状态并推送通知。
- **本地数据不上云**:录制、截图、运行报告全部留在本机;PII 自动脱敏(邮箱/手机号/身份证/银行卡),密码框明文不落盘。

## 功能总览

| 分组 | 能力 |
|---|---|
| 录制与编译 | 低级钩子录制(键鼠/窗口/滚动)、双击与连续输入合并、自窗口过滤、PII 脱敏、稳定 step_id + 场景分段 |
| 回放 | 定位链置信度回退、逐步重试、人工暂停/跳过/重试接管、断点续跑(按 step_id 对齐)、同窗口多段输入保真、回放前置门禁(目标窗口指纹) |
| 治理 | 方案审阅与批准(provenance 哈希防篡改)、不可变版本快照 + diff、能力注册表(前置/后置/依赖)、能力覆盖表、失败回溯上一能力、any_of 双期望验证 |
| 调度与编排 | 定时(每天/每 N 分钟)、条件触发盯守(文件出现/窗口出现)、任务依赖链(成功/失败/总是)、回放队列(忙时排队不丢失) |
| 个人 Agent | CALL_USER 通知推送(Bark/Server酱/企业微信 webhook)、主动建议(失败模式挖掘)、有界用户记忆(100 条,可遗忘)、连接器权限分级(只读/代操作) |
| 隐私与审计 | 网络白名单、运行历史 SQLite 索引、审计摘要、golden-vs-buggy 回归基线、意图分析(启发式优先,LLM 仅润色) |
| 导出 | Skill.md(含触发条件、负向能力边界、意图)、automation.json(结构化触发器 + 自然语言镜像)、证据包 zip(文档+步骤截图) |

## 快速开始

### 源码运行

```bash
# 1. 准备 Python 3.11+ 虚拟环境
py -3 -m venv .venv
.venv/Scripts/pip install -r python/requirements.txt

# 2. 启动后端(源码模式,数据目录 = 仓库根)
.venv/Scripts/python python/server.py
# UI: http://127.0.0.1:18899/ui/
```

也可以直接双击 `start.bat`(控制台菜单见 `run.bat`)。

### 打包桌面应用

```bash
./scripts/build_app.sh        # 先构建 dist/tagent-server.exe(PyInstaller),再打 Tauri 安装包
# 产物: src-tauri/target/release/bundle/{nsis,msi}/
```

> 打包 exe 必须使用干净的最小依赖 venv(见 `python/requirements.txt`);系统 Python 里若装有 pandas/scipy 会使 exe 体积翻倍。

### 测试

```bash
./scripts/run_tests.sh        # 41 个单元测试(纯离线)
# 桌面 E2E(需真实桌面,手动运行):
#   python/tests/test_e2e_self.py        录制→回放→断点→调度 全链路
#   python/tests/test_target_e2e.py      靶标应用 + 门禁 + 基线
#   python/tests/test_instance_e2e.py    实例:写文档/模拟购物/模拟发邮件/队列+盯守+联动+推送
```

## 架构与目录

```
src/index.html            # 工作台 UI(Scout / 任务 / 运行历史 / 设置)
src-tauri/                # Tauri 桌面壳(生命周期 attach/detach,打包 sidecar)
python/server.py          # FastAPI 后端:录制/回放/队列/盯守/审计 (127.0.0.1:18899)
python/recorder.py        # 低级钩子录制 + PII 脱敏 + 截图敏感遮挡
python/compiler.py        # 事件 → task.json(合并/去噪/定位链/step_id/场景)
python/player.py          # 确定性回放(定位回退/重试/暂停/断点/CALL_USER)
python/services/          # 调度、能力、审阅、基线、推送、盯守、记忆、白名单等
python/tests/             # 单元测试 + e2e + fixtures(靶标应用)
```

**数据目录**:源码模式在仓库根(`recordings/`、`runs/`、`data/`,均已 gitignore);打包后 exe 使用 `%LOCALAPPDATA%\TAGENT`。

## 安全边界

- 后端仅监听 `127.0.0.1`;无任何遥测上报。
- 导出工件带 provenance 哈希;方案未审阅批准不可导出、不可定时。
- LLM 完全可选:未配置 `data/llm_config.json` 时,自愈与意图润色自动降级为纯启发式。
