"""Стойкость плотности во времени — защита от спуфинга.

По playbook плотность должна «отстояться»: снятая через несколько секунд
заявка — это не уровень, а приманка. Один REST-снимок этого не показывает,
поэтому плотность подтверждается только повторными наблюдениями.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.density import Density


@dataclass
class WallTrack:
    side: str
    price: float
    first_seen: float
    last_seen: float
    observations: int = 1
    max_size: float = 0.0
    last_size: float = 0.0
    max_share: float = 0.0

    @property
    def age_sec(self) -> float:
        return self.last_seen - self.first_seen

    @property
    def held_share(self) -> float:
        """Какая часть максимального объёма плотности ещё стоит."""
        if self.max_size <= 0:
            return 0.0
        return self.last_size / self.max_size

    def is_mature(self, min_observations: int, min_age_sec: float) -> bool:
        return self.observations >= min_observations and self.age_sec >= min_age_sec

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "price": self.price,
            "observations": self.observations,
            "age_sec": round(self.age_sec, 1),
            "held_share": round(self.held_share, 3),
            "max_size": self.max_size,
            "last_size": self.last_size,
        }


@dataclass
class WallTracker:
    """Хранит по символу текущую отслеживаемую плотность."""

    price_tol_pct: float = 0.05
    ttl_sec: float = 900.0
    _tracks: dict[str, WallTrack] = field(default_factory=dict)

    def _same_level(self, track: WallTrack, density: Density) -> bool:
        if track.side != density.side or track.price <= 0:
            return False
        shift = abs(density.price - track.price) / track.price * 100.0
        return shift <= self.price_tol_pct

    def observe(
        self, symbol: str, density: Density | None, now: float | None = None
    ) -> WallTrack | None:
        now = time.time() if now is None else now
        track = self._tracks.get(symbol)
        if density is None:
            # плотности нет — уровень снят, накопленная выдержка сгорает
            self._tracks.pop(symbol, None)
            return None
        if track is not None and self._same_level(track, density):
            if now - track.last_seen > self.ttl_sec:
                track = None
            else:
                track.last_seen = now
                track.observations += 1
                track.last_size = density.size
                track.max_size = max(track.max_size, density.size)
                track.max_share = max(track.max_share, density.depth_share)
                self._tracks[symbol] = track
                return track
        fresh = WallTrack(
            side=density.side,
            price=density.price,
            first_seen=now,
            last_seen=now,
            observations=1,
            max_size=density.size,
            last_size=density.size,
            max_share=density.depth_share,
        )
        self._tracks[symbol] = fresh
        return fresh

    def track(self, symbol: str) -> WallTrack | None:
        return self._tracks.get(symbol)

    def forget(self, symbol: str) -> None:
        self._tracks.pop(symbol, None)
