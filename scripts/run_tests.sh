#!/usr/bin/env bash
# 运行纯单元测试（无后端/真实应用依赖）
set -e
cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -f "$PY" ] || ! "$PY" --version >/dev/null 2>&1; then
    PY="$(command -v python.exe || command -v python || true)"
fi
if [ -z "$PY" ]; then
    echo "[ERROR] Python 3.11+ was not found"
    exit 1
fi

# Reject an interpreter older than the supported runtime.
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    echo "[ERROR] Python 3.11+ was not found"
    exit 1
fi

UNIT_TESTS=(
    python/tests/test_capability_registry.py
    python/tests/test_capability_routes.py
    python/tests/test_capability_verifier.py
    python/tests/test_missing_variable.py
    python/tests/test_scheduler_persistence.py
    python/tests/test_windows_autostart.py
    python/tests/test_backend_lifecycle.py
    python/tests/test_recording_lifecycle.py
    python/tests/test_checkpoints.py
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
    python/tests/test_pii_guard.py
    python/tests/test_frame_redactor.py
    python/tests/test_task_versions.py
    python/tests/evals/compiler_eval.py
    python/tests/test_web_channel.py
    python/tests/test_replay_control.py
    python/tests/test_replay_diagnostics.py
    python/tests/test_replay_failure_boundary.py
    python/tests/test_replay_gate.py
    python/tests/test_intent_analyst.py
    python/tests/test_regression_baseline.py
    python/tests/test_capability_deps.py
    python/tests/test_run_store.py
    python/tests/test_step_ids.py
    python/tests/test_compiler_seed.py
    python/tests/test_pii_corpus.py
    python/tests/test_agent_features.py
    python/tests/test_hardening.py
    python/tests/test_uitars_actions.py
    python/tests/test_visual_agent.py
    python/tests/test_outlook_fields.py
    python/tests/test_outlook_input.py
    python/tests/test_outlook_verify.py
)

for f in "${UNIT_TESTS[@]}"; do
    echo "== $f =="
    # Preserve the test process exit code while keeping output concise.
    set +e
    output=$("$PY" "$f" 2>&1)
    status=$?
    printf '%s\n' "$output" | tail -n 2
    set -e
    if [ "$status" -ne 0 ]; then
        echo "[ERROR] $f failed (exit $status)"
        exit "$status"
    fi
done
echo "全部单元测试通过"
