"""
RawLogger — append every server message to a JSONL file.

A new file is created each run (timestamped), so logs never overwrite each
other and you can replay/diff runs.

Each line is one JSON object:
{
  "ts":       1745000000.123,   // unix timestamp (float)
  "op":       "UnitsFromServer",
  "counter":  42,
  "unit_id":  0,
  "team":     "Meow",
  "extra":    { ... }           // parsed extraJson — this is where the data is
}

If extraJson isn't valid JSON the raw string goes into "extra_raw" instead.

Usage:
    logger = RawLogger()          # creates raw_YYYYMMDD_HHMMSS.jsonl
    logger = RawLogger("my.jsonl") # fixed path
    for msg in stream:
        logger.log(msg)           # call for EVERY message, before filtering
    logger.close()
"""

import json
import time
from datetime import datetime
from pathlib import Path


class RawLogger:
    def __init__(self, path: str | None = None):
        if path is None:
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"raw_{ts}.jsonl"
        self.path     = path
        self._f       = open(path, "w", buffering=1)  # line-buffered
        self._count   = 0
        self._ops: dict[str, int] = {}  # op → count, for summary
        print(f"[RawLogger] Writing to {Path(path).resolve()}")

    def log(self, msg) -> None:
        """
        Log one CommandMessage (protobuf object from the gRPC stream).
        Call this for every message before any operation filtering.
        """
        op = msg.operation
        self._count += 1
        self._ops[op] = self._ops.get(op, 0) + 1

        entry: dict = {
            "ts":      time.time(),
            "op":      op,
            "counter": msg.counter,
            "unit_id": msg.unitId,
            "team":    msg.teamName,
        }
        if msg.extraJson:
            try:
                entry["extra"] = json.loads(msg.extraJson)
            except json.JSONDecodeError:
                entry["extra_raw"] = msg.extraJson

        self._f.write(json.dumps(entry) + "\n")

    def close(self) -> None:
        self._f.close()
        print(f"[RawLogger] Closed {self.path} — {self._count} messages logged")
        print(f"[RawLogger] Op counts: {self._ops}")
