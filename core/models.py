"""Data models for Regal Pro scheduling engine."""

from dataclasses import dataclass, field
from datetime import time
from typing import Any, Dict, List, Optional, Set


@dataclass
class PathScore:
    score: float
    count: int
    hops: int
    miles: float
    gap: int
    duration: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            'score': self.score,
            'count': self.count,
            'hops': self.hops,
            'miles': self.miles,
            'gap': self.gap,
            'duration': self.duration,
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class SchedulerConstraints:
    """Constraints configuring the search space for itineraries."""
    theaters: List[str]
    primary_code: str = ""
    start: time = field(default_factory=lambda: time(0, 0))
    end: time = field(default_factory=lambda: time(23, 59))
    buffer: int = 15
    gap_cap: int = 120
    unlimited: bool = True
    fudge: bool = False
    break_after: Optional[int] = None
    long_buffer: int = 60
    formats: Optional[List[str]] = None
    strategy: str = "Minimize Days"
    max_per_day: int = 99
    extreme_mode: bool = False

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SchedulerConstraints":
        return cls(
            theaters=list(data.get('theaters', [])),
            primary_code=str(data.get('primary_code', '')),
            start=data.get('start', time(0, 0)),
            end=data.get('end', time(23, 59)),
            buffer=int(data.get('buffer', 15)),
            gap_cap=int(data.get('gap_cap', 120)),
            unlimited=bool(data.get('unlimited', True)),
            fudge=bool(data.get('fudge', False)),
            break_after=data.get('break_after'),
            long_buffer=int(data.get('long_buffer', 60)),
            formats=data.get('formats'),
            strategy=str(data.get('strategy', "Minimize Days")),
            max_per_day=int(data.get('max_per_day', 99)),
            extreme_mode=bool(data.get('extreme_mode', False)),
        )
