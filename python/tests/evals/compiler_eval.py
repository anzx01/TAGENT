"""compiler 编译质量评测基线（移植自 skill-recorder evals/ 的场景+rubric 模式）

设计原则：
- 确定性、无 LLM：合成事件流 fixture -> 跑真实 Compiler 管线 -> 规则打分
- 场景隔离：临时目录物化 events.json，不碰真实 recordings/
- pass 线：无 critical 失败 且 通过率 >= 0.8（skill-recorder 同款）
- 用途：compiler/locator 修改后跑快速评分，任一场景失败 exit 1 阻断发布

直接运行：python tests/evals/compiler_eval.py [--out results.json] [--only 场景id]
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from compiler import Compiler

PASS_LINE = 0.8


# ---------- fixture 构造器（对齐 recorder 真实输出字段） ----------

def sw(window, hwnd, t):
    return {"action": "switch_window", "t": t, "window": window,
            "hwnd": hwnd, "win_size": [800, 600]}


def click(window, hwnd, x, y, t, uia=None, win=(800, 600)):
    step = {"action": "click", "t": t, "window": window, "hwnd": hwnd,
            "screen_x": x, "screen_y": y,
            "client_x": x - 20, "client_y": y - 50,
            "win_size": list(win),
            "pct_x": round(x / 1920 * 100, 1), "pct_y": round(y / 1080 * 100, 1)}
    if uia:
        step["uia"] = uia
    return step


def typ(text, window, hwnd, t, uia_name=None):
    step = {"action": "input_text", "t": t, "text": text,
            "window": window, "hwnd": hwnd, "win_size": [800, 600]}
    if uia_name:
        step["uia"] = {"name": uia_name, "type": "Edit", "class": "Edit"}
    return step


def key(k, t):
    return {"action": "key_press", "t": t, "key": k}


def wait(window, duration, t):
    return {"action": "wait", "t": t, "duration": duration, "window": window}


def ck(name, ok, detail="", critical=True):
    return {"name": name, "pass": bool(ok), "detail": str(detail), "critical": critical}


def ordered_subsequence(texts, groups):
    """有序子序列校验（skill-recorder evals/scoring.ts 同款贪心指针）：
    每组同义词在 texts 中按顺序出现（允许后续组命中同一项或更后）"""
    ptr = 0
    for g in groups:
        found = next((i for i in range(ptr, len(texts))
                      if any(w in texts[i] for w in g)), -1)
        if found < 0:
            return False, g
        ptr = found
    return True, None


def step_texts(task):
    """每步参与匹配的文本：action + text + 定位器摘要（不含解释性噪声）"""
    out = []
    for s in task.get("steps", []):
        loc = ""
        if s.get("locator"):
            first = s["locator"][0]
            loc = first.get("name") or first.get("text") or first.get("type", "")
        out.append(f"{s.get('action', '')} {s.get('text', '')} {loc}")
    return out


# ---------- 场景 ----------

def scenario_notepad_merge():
    events = [
        sw("无标题 - 记事本", 111, 0.1),
        click("无标题 - 记事本", 111, 100, 200, 1.0,
              uia={"name": "文本编辑器", "type": "Edit", "class": "Edit"}),
        typ("Hel", "无标题 - 记事本", 111, 2.0),
        typ("lo world", "无标题 - 记事本", 111, 3.0),
        key("ctrl+s", 4.0),
    ]

    def checks(task):
        acts = [s["action"] for s in task["steps"]]
        inputs = [s for s in task["steps"] if s["action"] == "input_text"]
        clicks = [s for s in task["steps"] if s["action"] == "click"]
        ok_order, miss = ordered_subsequence(
            step_texts(task),
            [["switch_window"], ["click"], ["input_text", "Hello world"], ["key_press"]])
        return [
            ck("actions_exact", acts == ["switch_window", "click", "input_text", "key_press"], acts),
            ck("input_merged", len(inputs) == 1 and inputs[0].get("text") == "Hello world",
               [s.get("text") for s in inputs]),
            ck("click_uia_top", bool(clicks) and clicks[0]["locator"][0].get("type") == "uia"
               and clicks[0]["locator"][0].get("confidence") == 0.95,
               clicks[0]["locator"][0] if clicks else None),
            ck("steps_ordered", ok_order, f"缺失组: {miss}"),
        ]

    return {"id": "notepad_input_merge", "title": "记事本输入合并+UIA定位", "events": events, "checks": checks}


def scenario_browser_param():
    events = [
        click("百度一下，你就知道 - Microsoft Edge", 222, 400, 300, 1.0,
              uia={"name": "搜索框", "type": "Edit", "class": "Edit"}),
        typ("高铁票查询", "百度一下，你就知道 - Microsoft Edge", 222, 2.0, uia_name="搜索框"),
    ]

    def checks(task):
        inputs = [s for s in task["steps"] if s["action"] == "input_text"]
        clicks = [s for s in task["steps"] if s["action"] == "click"]
        suggest = inputs[0].get("suggest_param") if inputs else None
        dom_types = [l.get("type") for l in (clicks[0].get("locator") if clicks else [])]
        return [
            ck("param_suggested", bool(suggest) and suggest.get("key") == "搜索"
               and suggest.get("var") == "{{搜索}}", suggest),
            ck("dom_locator_present", "dom" in dom_types, dom_types),
            ck("uia_first", dom_types and dom_types[0] == "uia", dom_types),
        ]

    return {"id": "browser_search_param", "title": "浏览器搜索参数化建议", "events": events, "checks": checks}


def scenario_noise():
    # pos A 两击间隔 0.3s(<0.5s) -> 合并为 double_click
    # pos B 第二击距保留击 0.7s(0.5~1.0s) -> 冗余过滤
    events = [
        click("无标题 - 记事本", 111, 100, 200, 1.0, uia={"name": "btn", "type": "Button", "class": "Button"}),
        click("无标题 - 记事本", 111, 100, 200, 1.3, uia={"name": "btn", "type": "Button", "class": "Button"}),
        click("无标题 - 记事本", 111, 500, 300, 2.0, uia={"name": "btn2", "type": "Button", "class": "Button"}),
        click("无标题 - 记事本", 111, 500, 300, 2.7, uia={"name": "btn2", "type": "Button", "class": "Button"}),
    ]

    def checks(task):
        acts = [s["action"] for s in task["steps"]]
        doubles = [s for s in task["steps"] if s["action"] == "double_click"]
        singles_b = [s for s in task["steps"]
                     if s["action"] == "click" and s.get("screen_x") == 500]
        return [
            ck("double_click_merged",
               acts.count("double_click") == 1 and doubles[0].get("screen_x") == 100, acts),
            ck("repeat_click_filtered", len(singles_b) == 1, len(singles_b)),
            ck("total_steps", len(task["steps"]) == 2, len(task["steps"])),
        ]

    return {"id": "noise_and_doubleclick", "title": "噪声过滤+双击合并", "events": events, "checks": checks}


def scenario_cross_app():
    events = [
        sw("无标题 - 记事本", 111, 0.1),
        click("无标题 - 记事本", 111, 100, 200, 1.0,
              uia={"name": "编辑区", "type": "Edit", "class": "Edit"}),
        sw("计算器", 222, 2.0),
        sw("计算器", 222, 2.5),
        click("计算器", 222, 50, 60, 3.0,
              uia={"name": "七", "type": "Button", "class": "Button"}),
    ]

    def checks(task):
        acts = [s["action"] for s in task["steps"]]
        switches = [s for s in task["steps"] if s["action"] == "switch_window"]
        return [
            ck("real_switch_kept", len(switches) == 2
               and switches[-1].get("window") == "计算器",
               [(s.get("window"), s.get("hwnd")) for s in switches]),
            ck("redundant_switch_filtered", acts.count("switch_window") == 2, acts),
            ck("segments", task.get("segment_count") == 2, task.get("segment_count")),
            ck("ordered", ordered_subsequence(
                step_texts(task), [["click"], ["switch_window", "计算器"], ["click"]])[0]),
        ]

    return {"id": "cross_app_switch", "title": "跨应用切换保留/冗余过滤", "events": events, "checks": checks}


def scenario_self_window():
    events = [
        click("智能操作助手 - 悬浮条", 999, 10, 10, 1.0),
        click("无标题 - 记事本", 111, 100, 200, 2.0,
              uia={"name": "btn", "type": "Button", "class": "Button"}),
        click("操作助手", 998, 20, 20, 3.0),
    ]

    def checks(task):
        windows = [s.get("window") for s in task["steps"]]
        return [
            ck("self_window_dropped",
               all(("助手" not in (w or "")) or ("记事本" in (w or "")) for w in windows),
               windows),
            ck("normal_step_kept", len(task["steps"]) == 1, len(task["steps"])),
        ]

    return {"id": "self_window_filter", "title": "自身窗口步骤过滤", "events": events, "checks": checks}


def scenario_weak_locator():
    events = [
        {"action": "click", "t": 1.0, "window": "某旧软件", "hwnd": 333,
         "screen_x": 100, "screen_y": 200},
        {"action": "click", "t": 2.0, "window": "某旧软件", "hwnd": 333,
         "screen_x": 300, "screen_y": 400},
    ]

    def checks(task):
        located = [s for s in task["steps"] if s.get("locator")]
        max_conf = max((s["locator"][0].get("confidence", 0) for s in located), default=1)
        return [
            ck("screen_coord_fallback", all(
                s["locator"] and s["locator"][-1].get("type") == "screen_coord"
                for s in task["steps"]), task["steps"][0].get("locator")),
            ck("weak_warning", task.get("warning") == "weak_locators",
               {"warning": task.get("warning"), "max_conf": max_conf}),
        ]

    return {"id": "weak_locator_warning", "title": "弱定位降级+警告", "events": events, "checks": checks}


def scenario_effects():
    events = [
        wait("无标题 - 记事本", 2.0, 0.5),
        click("无标题 - 记事本", 111, 100, 200, 3.0,
              uia={"name": "删除所选内容", "type": "MenuItem", "class": ""}),
        typ("hello", "无标题 - 记事本", 111, 4.0),
    ]

    def checks(task):
        effects = {s["action"] + "@" + str(s.get("t")): s.get("effect") for s in task["steps"]}
        by_action = {}
        for s in task["steps"]:
            by_action.setdefault(s["action"], s.get("effect"))
        return [
            ck("wait_readonly", by_action.get("wait") == "readonly", by_action),
            ck("delete_destructive", by_action.get("click") == "destructive", by_action),
            ck("input_mutating", by_action.get("input_text") == "mutating", by_action),
        ]

    return {"id": "effect_labels", "title": "安全效果标签", "events": events, "checks": checks}


SCENARIOS = [
    scenario_notepad_merge(),
    scenario_browser_param(),
    scenario_noise(),
    scenario_cross_app(),
    scenario_self_window(),
    scenario_weak_locator(),
    scenario_effects(),
]


# ---------- 运行器 ----------

def run_scenario(sc, root: Path) -> dict:
    d = root / sc["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.json").write_text(
        json.dumps({"steps": sc["events"], "count": len(sc["events"])}, ensure_ascii=False),
        encoding="utf-8")
    Compiler(str(d)).compile()  # 真实编译管线
    task = json.loads((d / "task.json").read_text(encoding="utf-8"))
    checks = sc["checks"](task)
    passed = sum(1 for c in checks if c["pass"])
    critical_fail = any(not c["pass"] and c["critical"] for c in checks)
    score = passed / len(checks) if checks else 1.0
    return {
        "id": sc["id"], "title": sc["title"],
        "pass": (not critical_fail) and score >= PASS_LINE,
        "score": round(score, 3), "checks": checks,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="结果 JSON 输出路径")
    ap.add_argument("--only", help="只跑指定 id（逗号分隔）")
    args = ap.parse_args()

    selected = SCENARIOS
    if args.only:
        want = {x.strip() for x in args.only.split(",")}
        selected = [s for s in SCENARIOS if s["id"] in want]

    results = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for sc in selected:
            r = run_scenario(sc, root)
            results.append(r)
            mark = "PASS" if r["pass"] else "FAIL"
            print(f"[{mark}] {r['id']:<24} score={r['score']:.2f} {r['title']}")
            for c in r["checks"]:
                if not c["pass"]:
                    print(f"    ✗ {c['name']}: {c['detail']}")

    passed_n = sum(1 for r in results if r["pass"])
    print(f"\n{passed_n}/{len(results)} 场景通过（pass 线 {PASS_LINE}）")

    payload = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "pass_line": PASS_LINE, "results": results,
               "all_pass": passed_n == len(results)}
    out = Path(args.out) if args.out else \
        Path(__file__).parent / "results" / f"{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入 {out}")

    sys.exit(0 if payload["all_pass"] else 1)


if __name__ == "__main__":
    main()
