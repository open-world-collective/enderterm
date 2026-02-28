from __future__ import annotations

from enderterm import datapack_viewer


class _Owner:
    def __init__(self) -> None:
        self._present_cache_tex = object()


class _Companion:
    def __init__(self, owner: object) -> None:
        self._owner = owner

    def __getattr__(self, name: str) -> object:
        return getattr(self._owner, name)


def test_instance_dict_get_bypasses_getattr_delegation() -> None:
    owner = _Owner()
    companion = _Companion(owner)

    assert getattr(companion, "_present_cache_tex") is owner._present_cache_tex
    assert datapack_viewer._instance_dict_get(companion, "_present_cache_tex") is None

    sentinel = object()
    companion._present_cache_tex = sentinel
    assert datapack_viewer._instance_dict_get(companion, "_present_cache_tex") is sentinel


def test_strip_fade_target_alpha_clamps_and_quantizes() -> None:
    fn = datapack_viewer._strip_fade_target_alpha
    cache_clear = getattr(fn, "cache_clear")
    cache_clear()

    # Below fade band -> transparent, above band -> opaque.
    assert fn(-100, 0, 10, 8) == 0
    assert fn(100, 0, 10, 8) == 255

    # With 2 quantization levels, side-fade alpha collapses to endpoints.
    assert fn(0, 0, 10, 2) in {0, 255}
    assert fn(9, 0, 10, 2) in {0, 255}

    cache_clear()


def test_strip_fade_target_alpha_uses_lru_cache() -> None:
    fn = datapack_viewer._strip_fade_target_alpha
    cache_clear = getattr(fn, "cache_clear")
    cache_info = getattr(fn, "cache_info")
    cache_clear()

    a = fn(5, 0, 12, 6)
    b = fn(5, 0, 12, 6)
    assert a == b
    info = cache_info()
    assert info.misses == 1
    assert info.hits >= 1

    cache_clear()


def test_strip_fade_side_alpha_cached_matches_target_alpha() -> None:
    cache: dict[int, int] = {}
    for y in (-17, -1, 0, 5, 42):
        got = datapack_viewer._strip_fade_side_alpha_cached(
            y,
            bottom_y_base=-8,
            strip_fade_h=16,
            strip_fade_levels=8,
            cache=cache,
        )
        expected = datapack_viewer._strip_fade_target_alpha(y, -8, 16, 8)
        assert got == expected


def test_strip_fade_side_alpha_cached_reuses_local_y_cache() -> None:
    cache: dict[int, int] = {}

    a = datapack_viewer._strip_fade_side_alpha_cached(
        7,
        bottom_y_base=0,
        strip_fade_h=10,
        strip_fade_levels=6,
        cache=cache,
    )
    b = datapack_viewer._strip_fade_side_alpha_cached(
        7,
        bottom_y_base=0,
        strip_fade_h=10,
        strip_fade_levels=6,
        cache=cache,
    )

    assert a == b
    assert cache == {7: int(a)}


def test_resolve_present_cache_viewport_px_prefers_render_cap_cached_size() -> None:
    class _Owner:
        _render_cap_last_view_px = (1600, 900)

        def get_viewport_size(self) -> tuple[int, int]:
            raise AssertionError("get_viewport_size should not be called when cached viewport is valid")

    assert datapack_viewer._resolve_present_cache_viewport_px(_Owner()) == (1600, 900)


def test_resolve_present_cache_viewport_px_falls_back_to_live_viewport() -> None:
    class _Owner:
        _render_cap_last_view_px = (0, 0)

        def __init__(self) -> None:
            self.calls = 0

        def get_viewport_size(self) -> tuple[int, int]:
            self.calls += 1
            return (801, 599)

    owner = _Owner()
    assert datapack_viewer._resolve_present_cache_viewport_px(owner) == (801, 599)
    assert owner.calls == 1
