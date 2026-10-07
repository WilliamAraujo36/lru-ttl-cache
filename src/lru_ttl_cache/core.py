"""Core implementation of a least-recently-used cache with per-entry TTL expiry.

Design choices:

- The cache is bounded by a maximum number of entries. When a new entry is
  inserted that would exceed the bound, the least-recently-used entry is
  evicted first. Entries whose TTL has already expired are evicted before
  falling back to plain LRU eviction.

- TTL is an absolute deadline (``now() + ttl``) stored per entry, not a
  remaining-time counter. This means a single monotonic clock call can decide
  whether an entry is stale, and there is no per-entry "last access" timer
  that has to be recomputed as the clock advances.

- LRU recency is tracked with an ``OrderedDict``. On every access (read or
  write) the touched entry is moved to the end, so the front of the dict is
  always the least-recently-used entry. We cannot rely on dict iteration order
  alone for recency because the TTL-cleanup sweep touches entries and would
  disturb their ordering if we were not explicit.

- The clock is injectable. The default is ``time.monotonic`` because it is
  immune to system clock adjustments, which is what you want for expiry
  arithmetic. Tests pass a fake clock so they never depend on wall time.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable, Iterator, Optional, TypeVar


K = TypeVar("K")
V = TypeVar("V")


class LRUTTLCache:
    """A bounded least-recently-used cache where entries also expire on a clock.

    Args:
        max_size: Maximum number of live entries. Must be at least 1.
        ttl: Time-to-live for each entry, in clock units. Must be positive.
        clock: Zero-argument callable returning a number representing "now".
          Defaults to ``time.monotonic`` so that expiry is immune to system
          clock jumps. Pass a fake in tests.

    Raises:
        TypeError: If ``max_size`` or ``ttl`` is not an int/float, or
          ``clock`` is not callable.
        ValueError: If ``max_size`` or ``ttl`` is not positive and finite.
    """

    def __init__(
        self,
        max_size: int,
        ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(max_size, int) or isinstance(max_size, bool):
            raise TypeError(
                f"max_size must be an int, got {type(max_size).__name__}"
            )
        if max_size < 1:
            raise ValueError(f"max_size must be at least 1, got {max_size}")
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
            raise TypeError(f"ttl must be a number, got {type(ttl).__name__}")
        # Comparing floats directly for zero/negativity is safe here: we are
        # not measuring elapsed time, just guarding the constructor argument.
        if ttl <= 0:
            raise ValueError(f"ttl must be positive, got {ttl}")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._max_size = max_size
        self._ttl = float(ttl)
        self._clock = clock
        # Maps key -> (value, expires_at). Front is least-recently-used.
        self._data: "OrderedDict[K, tuple[V, float]]" = OrderedDict()

    # -- properties ---------------------------------------------------------

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def ttl(self) -> float:
        return self._ttl

    def __len__(self) -> int:
        self._purge_expired()
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        now = self._clock()
        entry = self._data.get(key)  # type: ignore[arg-type]
        if entry is None:
            return False
        _, expires_at = entry
        if expires_at <= now:
            del self._data[key]  # type: ignore[arg-type]
            return False
        return True

    # -- core mutation ------------------------------------------------------

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:
        """Return the value for ``key`` if present and unexpired, else ``default``.

        A successful access counts as a use for LRU ordering.
        """
        now = self._clock()
        entry = self._data.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if expires_at <= now:
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __getitem__(self, key: K) -> V:
        """Return the value for ``key`` or raise ``KeyError`` if missing/expired."""
        now = self._clock()
        entry = self._data.get(key)
        if entry is None:
            raise KeyError(key)
        value, expires_at = entry
        if expires_at <= now:
            del self._data[key]
            raise KeyError(key)
        self._data.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        """Insert or update ``key`` with ``value``, refreshing its TTL.

        The entry's expiry deadline is computed from the current clock reading,
        so every set (including updates) resets the TTL window.
        """
        now = self._clock()
        expires_at = now + self._ttl

        # Updating an existing key: just overwrite the stored tuple and move
        # it to the most-recently-used position. No size change, so no purge
        # or eviction is needed.
        if key in self._data:
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            return

        # New entry. First drop any already-expired entries so we do not evict
        # a live entry while there is dead weight sitting around.
        self._purge_expired()

        if len(self._data) >= self._max_size:
            # popitem(last=False) removes the least-recently-used entry.
            self._data.popitem(last=False)

        self._data[key] = (value, expires_at)

    def __setitem__(self, key: K, value: V) -> None:
        self.set(key, value)

    def delete(self, key: K) -> None:
        """Remove ``key``. Raises ``KeyError`` if it is absent.

        An entry that exists but is expired is still considered present for the
        purposes of deletion — it is removed from storage and no error is
        raised. The distinction matters because it keeps ``delete`` an honest
        "I want this gone" operation regardless of the clock.
        """
        try:
            del self._data[key]
        except KeyError:
            raise KeyError(key)

    def __delitem__(self, key: K) -> None:
        self.delete(key)

    def clear(self) -> None:
        """Drop every entry regardless of recency or expiry."""
        self._data.clear()

    def keys(self) -> Iterator[K]:
        """Iterate over currently-live keys in LRU order (least-recent first).

        Expired entries are removed at the start of iteration but not lazily
        during it, so callers get a stable snapshot per call.
        """
        self._purge_expired()
        return iter(list(self._data.keys()))

    # -- internal -----------------------------------------------------------

    def _purge_expired(self) -> None:
        """Remove every entry whose expiry deadline is at or before ``now``.

        We mutate a copy of the keys to avoid changing dict size during
        iteration; ``OrderedDict`` does not expose a safe cursor for this.
        """
        now = self._clock()
        # Build the list first so deletion does not invalidate the iterator.
        for key in list(self._data.keys()):
            _, expires_at = self._data[key]
            if expires_at <= now:
                del self._data[key]
