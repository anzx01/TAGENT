"""UI-TARS action syntax adapter; never executes model-generated Python.

Protocol reference: bytedance/UI-TARS codes/ui_tars/action_parser.py.
Coordinates are converted to the supplied screenshot's pixel space.
"""
import ast
import math
import re


def parse_action(text, width, height, coordinate_mode="pixels"):
    if width <= 0 or height <= 0:
        raise ValueError("Invalid screenshot dimensions")
    scales = {"pixels": (width, height), "normalized": (1, 1), "1000": (1000, 1000)}
    if coordinate_mode not in scales:
        raise ValueError("Unknown coordinate mode")
    source = text.split("Action:", 1)[-1].strip()
    node = ast.parse(source, mode="eval").body
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.args:
        raise ValueError("Expected a single named action")
    args = {}
    for kw in node.keywords:
        if kw.arg is None or kw.arg in args:
            raise ValueError("Invalid action arguments")
        args[kw.arg] = ast.literal_eval(kw.value)

    def point(value):
        if isinstance(value, str):
            value = re.sub(r"</?point>|<\|box_start\|>|<\|box_end\|>", "", value).strip()
            value = ast.literal_eval(value) if value.startswith(("(", "[")) else tuple(map(float, value.split()))
        if not isinstance(value, (tuple, list)) or len(value) not in (2, 4):
            raise ValueError("Invalid point or box")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
            raise ValueError("Non-finite coordinate")
        sx, sy = scales[coordinate_mode]
        if any(v < 0 or v > (sx if i % 2 == 0 else sy) for i, v in enumerate(value)):
            raise ValueError("Coordinate outside screenshot")
        x, y = value[:2] if len(value) == 2 else ((value[0]+value[2])/2, (value[1]+value[3])/2)
        return min(width-1, round(x*width/sx)), min(height-1, round(y*height/sy))

    name = node.func.id
    clicks = {"click": "click", "left_double": "double_click", "right_single": "right_click"}
    if name in clicks:
        x, y = point(args.get("point", args.get("start_box")))
        return {"action": clicks[name], "x": x, "y": y}
    if name == "type" and isinstance(args.get("content"), str):
        return {"action": "type", "text": args["content"]}
    if name == "hotkey" and isinstance(args.get("key"), str):
        return {"action": "hotkey", "keys": "+".join(args["key"].lower().split())}
    if name == "drag":
        x, y = point(args.get("start_point", args.get("start_box"))); ex, ey = point(args.get("end_point", args.get("end_box")))
        return {"action": "drag", "from_x": x, "from_y": y, "to_x": ex, "to_y": ey}
    if name == "wait" and not args:
        return {"action": "wait", "seconds": 5}
    if name == "finished":
        return {"action": "done", "summary": str(args.get("content", ""))}
    raise ValueError("Unsupported UI-TARS action")
