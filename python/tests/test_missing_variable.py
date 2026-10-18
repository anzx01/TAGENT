"""An unresolved template must never be typed into another application."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import Player


def main():
    player = Player.__new__(Player)
    player.variables = {}
    player.var_sources = {}
    result = player._do_input_text({"text": "订单 {{ order_id }}"})
    assert not result.ok and result.reason == "missing_variable"
    print("missing_variable PASS")


if __name__ == "__main__":
    main()
