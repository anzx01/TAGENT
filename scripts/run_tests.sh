#!/usr/bin/env bash
# 运行纯单元测试（无后端/真实应用依赖）
set -e
cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -f "$PY" ] || ! "$PY" --version >/dev/null 2>&1; then
    PY="$(command -v python || true)"
fi
if [ -z "$PY" ]; then
    echo "[ERROR] Python 3.11+ was not found"
    exit 1
fi

UNIT_TESTS=(
    python/tests/test_capability_registry.py
    python/tests/test_compiler_semantic.py
    python/tests/test_self_window_filter.py
    python/tests/test_idempotent_replay.py
    python/tests/test_desktop_target.py
    python/tests/test_correction_governance.py
    python/tests/test_run_context.py
    python/tests/test_plan_invalidation.py
    python/tests/test_eval_scorer.py
    python/tests/test_workflow_plan.py
    python/tests/test_workflow_artifacts.py
    python/tests/test_discovery.py
    python/tests/test_merge_warning.py
    python/tests/test_pii_guard.py
    python/tests/test_frame_redactor.py
    python/tests/test_task_versions.py
    python/tests/evals/compiler_eval.py
    python/tests/test_web_channel.py
    python/tests/test_replay_control.py
    python/tests/test_workspace_ui.py
)

for f in "${UNIT_TESTS[@]}"; do
    echo "== $f =="
    "$PY" "$f" 2>&1 | tail -n 2
done
echo "全部单元测试通过"
