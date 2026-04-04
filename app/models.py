# app/models.py
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Episode:
    season: int
    episode: int
    title: str
    duration_min: Optional[int] = None          # ← то, что нам нужно
    episode_id: Optional[str] = None             # imdb ttXXXXX

@dataclass
class ScrapeResult:
    series_title: Optional[str] = None          # ← новое!
    episodes: List[Episode] = None              # или field(default_factory=list)
