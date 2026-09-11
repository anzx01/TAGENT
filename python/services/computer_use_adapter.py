"""Computer-use adapter: local screen evidence plus optional vision-model planning."""
from __future__ import annotations
import base64, json
from pathlib import Path
from services.computer_use_visual import screenshot, ocr_words

class ComputerUseAdapter:
    def observe(self, evidence_path: str | Path | None = None) -> dict:
        image = screenshot(evidence_path)
        return {"screenshot": str(evidence_path) if evidence_path else None,
                "ocr": ocr_words(image)}

    def plan(self, goal: str, evidence_path: str | Path) -> dict | None:
        from services.llm_heal import get_healer
        healer = get_healer()
        if not healer.enabled:
            return None
        raw = Path(evidence_path).read_bytes()
        return healer.plan_visual(goal, raw)
