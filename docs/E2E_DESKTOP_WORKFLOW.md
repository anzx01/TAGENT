# 桌面新建文本文件回归记录

## 场景

启动 TAGENT，点击录制；关闭目标窗口；在桌面右键选择“新建 -> 文本文档”；返回 TAGENT 点击停止；点击回放。

## 根因

浏览器承载的 TAGENT UI（标题为“智能操作助手 - Brave”）被低级钩子当成了业务窗口，导致录制产物包含：

- 程序自身窗口的关闭按钮
- 程序自身的停止按钮

回放时第一个动作会尝试关闭控制 UI，后续状态轮询和回放自然失真。

## 修复

- 录制阶段过滤 TAGENT 自身窗口事件。
- 编译阶段再次过滤，兼容旧录制。
- 回放前检测到旧任务包含自身窗口时自动重新编译清理。
- 桌面坐标继续使用屏幕百分比；窗口内坐标优先使用窗口百分比。
- 多个同类窗口存在时按 Z 序选择可见窗口，不再直接卡死等待人工。

## 实测结果（2026-09-05）

- 录制：`20260905_071000`
- 清理前：8 步（含 TAGENT/Brave 自身动作）
- 清理后：6 步
- 回放：`完成 6/6 步`
- 评测：`verdict=pass`、`sequence_ratio=1.0`、`action_accuracy=1.0`
- UI 回放 E2E：通过

## 相关回归命令

```powershell
python python/tests/test_ui_replay_e2e.py
python python/tests/test_replay_control.py
python python/tests/test_compiler_semantic.py
```

## Replay idempotency

The final desktop replay also covers the case where the two recorded VS Code
windows have already been closed before replay starts. A recorded click in the
top-right title-bar close region is reported as `skip_missing_window` (the
desired closed state is already true); an ordinary missing target still enters
the takeover flow. This keeps the workflow deterministic without weakening
window checks for normal actions.

The E2E harness also preflights the interactive Windows Shell. A headless or
non-interactive session is reported as blocked instead of treating physical
mouse injection as success.

## Native package build

Rust stable MSVC 1.98.1 and Visual Studio 2022 C++ Build Tools were installed
and the Tauri debug build completed. The generated packages are:

- `src-tauri/target/debug/bundle/nsis/tagent_0.1.0_x64-setup.exe`
- `src-tauri/target/debug/bundle/msi/tagent_0.1.0_x64_en-US.msi`

The package files were produced by Tauri's NSIS and WiX bundlers and passed
basic PE/MSI signature checks.
