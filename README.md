# LRU TTL Cache

A small, standard-library-only Python cache that bounds size by least-recently-used eviction and also drops entries after a fixed time-to-live. The clock is injectable so expiry is deterministic and testable without sleeping.

## Usage

```python
from lru_ttl_cache import LRUTTLCache

cache = LRUTTLCache(max_size=128, ttl=60.0)

cache.set("user:42", {"name": "Ada"})
user = cache.get("user:42")        # -> {"name": "Ada"} (or None if missing/expired)

cache["user:43"] = {"name": "Grace"}
del cache["user:42"]               # raises KeyError if absent
cache.clear()                      # drop everything

for key in cache.keys():           # live keys, least-recent first
    ...
```

The constructor takes an optional `clock` callable (default `time.monotonic`) returning the current time as a number. Pass a fake in tests; never assert on wall-clock time.

Exported names:

- `LRUTTLCache(max_size: int, ttl: float, clock=time.monotonic)`
- `LRUTTLCache.get(key, default=None) -> value | default`
- `LRUTTLCache[key]` / `LRUTTLCache[key] = value` / `del LRUTTLCache[key]`
- `LRUTTLCache.set(key, value)`
- `LRUTTLCache.delete(key)`
- `LRUTTLCache.clear()`
- `LRUTTLCache.keys()`
- `len(LRUTTLCache)`, `key in LRUTTLCache`
- `LRUTTLCache.max_size`, `LRUTTLCache.ttl`

## Why this exists

Some caches need both a hard size cap (so memory is bounded) and a freshness deadline (so stale data does not outlive its source). Doing only one is easy: a plain dict with a size cap, or a dict that sweeps on TTL. Doing both means that on insert you may have to choose between evicting the least-recently-used live entry and reaping an already-expired entry that is still occupying a slot. This library reaps expired entries first, then falls back to LRU eviction. That is the only deliberate policy choice in here; if you need a different one, copy the core (it is one file).

The trade-off is simplicity over throughput: every `set` does a sweep of all entries to reap expired ones before deciding whether to evict. For workloads where the cache is mostly full of live entries this is fine; for very large caches with a high write rate you would want a hashed expiry wheel instead.

## The awkward edge

An entry that has expired is still physically present in the underlying storage until something touches it (a `get`, `set`, `len`, `keys`, or `in` check). `delete()` does not consult the clock: deleting an expired-but-present entry succeeds, deleting a never-inserted key raises `KeyError`. If you need a guaranteed-empty-after-TTL invariant, call `len(cache)` or `cache.keys()` to force a sweep — do not rely on expired entries vanishing on their own.

## Running the tests

```
PYTHONPATH=src python -m unittest discover -s tests
```

No third-party dependencies. Python 3.8+.
