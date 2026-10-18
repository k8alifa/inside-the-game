"""Engine constants that drive match totals, in one place.

Defaults equal the literals the engine has always used, so a default
SimConfig reproduces every existing match byte for byte. Calibration
(tools/calibrate) searches over these and writes a JSON file that
`itg-sim generate --calibration <file>` loads.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class SimConfig:
    shot_base: float = 0.026         # shot probability in the final third, base
    shot_slope: float = 0.145        # extra shot probability as the ball nears goal
    finish_scale: float = 1.0        # multiplier on chance conversion
    foul_base: float = 0.0145        # defensive foul rate per action
    card_base: float = 0.19          # card probability per foul
    corner_clearance: float = 0.12   # final-third pass cut out for a corner
    corner_after_shot: float = 0.40  # saved or blocked shot that goes behind
    pass_loss_base: float = 0.065    # base turnover rate per pass or carry
    home_advantage: float = 0.0      # extra shot and finishing rate for the home side

    def is_default(self) -> bool:
        return self == SimConfig()

    def to_dict(self) -> dict:
        return asdict(self)

    def config_hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    @classmethod
    def from_dict(cls, data: dict) -> "SimConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown SimConfig fields: {', '.join(sorted(unknown))}")
        return cls(**{k: float(v) for k, v in data.items()})

    @classmethod
    def load(cls, path: str | Path) -> "SimConfig":
        data = json.loads(Path(path).read_text())
        return cls.from_dict(data.get("config", data))
