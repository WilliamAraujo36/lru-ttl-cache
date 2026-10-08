import unittest

from lru_ttl_cache import LRUTTLCache


class FakeClock:
    """Deterministic clock for tests."""

    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, by):
        self.now += float(by)


class TestConstruction(unittest.TestCase):
    def test_default_clock_is_monotonic(self):
        cache = LRUTTLCache(max_size=4, ttl=10.0)
        # The default clock must read as a real monotonic timestamp, i.e.
        # strictly positive on every supported platform.
        self.assertGreater(cache._clock(), 0.0)

    def test_rejects_non_positive_max_size(self):
        with self.assertRaises(ValueError):
            LRUTTLCache(max_size=0, ttl=1.0)
        with self.assertRaises(ValueError):
            LRUTTLCache(max_size=-3, ttl=1.0)

    def test_rejects_non_positive_ttl(self):
        with self.assertRaises(ValueError):
            LRUTTLCache(max_size=4, ttl=0)
        with self.assertRaises(ValueError):
            LRUTTLCache(max_size=4, ttl=-0.5)

    def test_rejects_bad_types(self):
        with self.assertRaises(TypeError):
            LRUTTLCache(max_size=2.5, ttl=1.0)
        with self.assertRaises(TypeError):
            LRUTTLCache(max_size=True, ttl=1.0)  # bool is not an int here
        with self.assertRaises(TypeError):
            LRUTTLCache(max_size=4, ttl="soon")
        with self.assertRaises(TypeError):
            LRUTTLCache(max_size=4, ttl=1.0, clock="not callable")


class TestGetSet(unittest.TestCase):
    def test_set_and_get(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=clock)
        c.set("a", 1)
        self.assertEqual(c.get("a"), 1)
        self.assertEqual(c["a"], 1)

    def test_get_missing_returns_default(self):
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=FakeClock(0.0))
        self.assertIsNone(c.get("missing"))
        self.assertEqual(c.get("missing", 42), 42)

    def test_getitem_missing_raises_keyerror(self):
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=FakeClock(0.0))
        with self.assertRaises(KeyError):
            _ = c["missing"]

    def test_set_updates_value_and_refreshes_ttl(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=clock)
        c.set("a", 1)
        clock.advance(9.0)
        c.set("a", 2)  # refresh
        clock.advance(2.0)
        # Original deadline would have been t=10; refreshed deadline is t=21.
        self.assertEqual(c.get("a"), 2)

    def test_setitem_is_alias_for_set(self):
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=FakeClock(0.0))
        c["a"] = 1
        self.assertEqual(c["a"], 1)


class TestExpiry(unittest.TestCase):
    def test_entry_expires_after_ttl(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=clock)
        c.set("a", 1)
        clock.advance(9.0)
        self.assertEqual(c.get("a"), 1)  # still alive at t=9
        clock.advance(1.0)  # t=10, deadline reached
        self.assertIsNone(c.get("a"))  # expired

    def test_getitem_on_expired_raises_keyerror(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=5.0, clock=clock)
        c.set("a", 1)
        clock.advance(5.0)
        with self.assertRaises(KeyError):
            _ = c["a"]

    def test_contains_respects_expiry(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=clock)
        c.set("a", 1)
        self.assertIn("a", c)
        clock.advance(10.0)
        self.assertNotIn("a", c)

    def test_len_excludes_expired(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=clock)
        c.set("a", 1)
        c.set("b", 2)
        clock.advance(10.0)
        self.assertEqual(len(c), 0)

    def test_keys_excludes_expired(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=clock)
        c.set("a", 1)
        c.set("b", 2)
        clock.advance(10.0)
        self.assertEqual(list(c.keys()), [])


class TestLRUEviction(unittest.TestCase):
    def test_evicts_least_recently_used_when_full(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=2, ttl=100.0, clock=clock)
        c.set("a", 1)
        c.set("b", 2)
        # Touch "a" so "b" becomes least-recently-used.
        _ = c.get("a")
        c.set("c", 3)  # must evict "b"
        self.assertIsNone(c.get("b"))
        self.assertEqual(c.get("a"), 1)
        self.assertEqual(c.get("c"), 3)

    def test_eviction_prefers_expiring_entries(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=2, ttl=100.0, clock=clock)
        c.set("a", 1)
        clock.advance(10.0)
        c.set("b", 2)
        # "a" is now closer to expiry but still live; inserting "c" should
        # purge nothing (neither has expired) and evict the LRU entry, which
        # is "a".
        c.set("c", 3)
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("b"), 2)
        self.assertEqual(c.get("c"), 3)

    def test_expired_entries_purged_before_eviction(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=2, ttl=10.0, clock=clock)
        c.set("a", 1)
        clock.advance(10.0)  # "a" now expired
        c.set("b", 2)
        c.set("c", 3)  # would normally evict; but "a" is dead weight
        # "a" should have been purged, not "b".
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("b"), 2)
        self.assertEqual(c.get("c"), 3)

    def test_update_does_not_evict(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=1, ttl=10.0, clock=clock)
        c.set("a", 1)
        c.set("a", 2)  # update, not insert
        self.assertEqual(len(c), 1)
        self.assertEqual(c.get("a"), 2)


class TestDeleteAndClear(unittest.TestCase):
    def test_delete_existing(self):
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=FakeClock(0.0))
        c.set("a", 1)
        c.delete("a")
        self.assertNotIn("a", c)

    def test_delete_missing_raises_keyerror(self):
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=FakeClock(0.0))
        with self.assertRaises(KeyError):
            c.delete("nope")

    def test_delitem_is_alias(self):
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=FakeClock(0.0))
        c["a"] = 1
        del c["a"]
        self.assertNotIn("a", c)

    def test_delete_expired_entry_does_not_raise(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=5.0, clock=clock)
        c.set("a", 1)
        clock.advance(5.0)  # expired, but still physically present
        # delete() does not consult the clock; an expired-but-present entry is
        # removable without error.
        c.delete("a")
        self.assertNotIn("a", c)

    def test_clear(self):
        c = LRUTTLCache(max_size=4, ttl=10.0, clock=FakeClock(0.0))
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        self.assertEqual(len(c), 0)
        self.assertIsNone(c.get("a"))


class TestKeysOrder(unittest.TestCase):
    def test_keys_in_lru_order(self):
        clock = FakeClock(0.0)
        c = LRUTTLCache(max_size=4, ttl=100.0, clock=clock)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        # Touch "a"; order from least-recent should now be b, c, a.
        _ = c.get("a")
        self.assertEqual(list(c.keys()), ["b", "c", "a"])


if __name__ == "__main__":
    unittest.main()
