# app/model_interface.py
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml


class Model(ABC):
    @abstractmethod
    def decide(self, seq: int, context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class FakeModel(Model):
    """
    Replays scripted decisions from a YAML file.

    Script format:

    - tool: files.read
      args:
        path: README.md

    - finish:
        result:
          done: true
    """

    def __init__(self, script_path: Path):
        self.script_path = Path(script_path)
        self.script = yaml.safe_load(self.script_path.read_text(encoding="utf-8"))

        if not isinstance(self.script, list):
            raise ValueError("FakeModel script must be a YAML list")

    def decide(self, seq: int, context: dict[str, Any]) -> dict[str, Any]:
        if seq > len(self.script):
            return {
                "action": "finish",
                "result": {
                    "reason": "script_end",
                },
            }

        item = self.script[seq - 1]

        if "finish" in item:
            finish = item.get("finish") or {}
            return {
                "action": "finish",
                "result": finish.get("result", {}),
            }

        if "tool" not in item:
            raise ValueError("Script item must contain 'tool' or 'finish'")

        return {
            "action": "tool_call",
            "tool": item["tool"],
            "args": item.get("args", {}),
        }
