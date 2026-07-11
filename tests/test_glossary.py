"""Tests for the semantic-layer glossary loader.

Focus: the loader must be Traversable-native so the bundled 1,694-line
``glossary.yaml`` survives being imported from a zipped / non-filesystem loader
(Codex review finding X-1). The prior implementation stringified the Traversable
into a ``pathlib.Path`` and called ``path.exists()`` — always False under a zip
importer — silently dropping the entire canonical glossary to the offline
fallback with no signal.
"""
from __future__ import annotations

import importlib.resources

import pytest

from medecon_verify import glossary


@pytest.fixture(autouse=True)
def _clear_parse_cache():
    """Each test starts with a cold parse cache so monkeypatched loaders take."""
    glossary._PARSE_CACHE.clear()
    yield
    glossary._PARSE_CACHE.clear()


class _FakeTraversable:
    """A Traversable that is NOT backed by a real filesystem path.

    Simulates the ``importlib.resources`` object a zip / wheel loader returns:
    ``is_file()`` is True and ``read_text()`` yields the bundled bytes, but
    ``str(self)`` is not a path that ``pathlib.Path(...).exists()`` will ever
    resolve — the exact scenario the old ``path.exists()`` gate dropped.
    """

    def __init__(self, text: str):
        self._text = text

    def __truediv__(self, _other):  # supports `files(...) / "data" / "glossary.yaml"`
        return self

    def joinpath(self, *_parts):
        return self

    def is_file(self) -> bool:
        return True

    def read_text(self, encoding: str = "utf-8") -> str:
        assert encoding == "utf-8"
        return self._text

    def __str__(self) -> str:  # not a real, stat()-able path
        return "medecon_verify.zip://data/glossary.yaml"


_TINY_GLOSSARY = (
    "FAKETERM:\n"
    "  definition: A synthetic term proving the external glossary loaded.\n"
    "  see_also: [PMPM]\n"
    "837:\n"                       # bare-numeric key: must normalize to str
    "  definition: Institutional claim EDI transaction.\n"
)


def test_external_glossary_loads_from_non_filesystem_traversable(monkeypatch):
    """The bug Codex proved: a zip-loader Traversable (is_file True, no on-disk
    path) must still load the bundled glossary rather than silently returning {}.
    """
    pytest.importorskip("yaml")  # parsing the loaded bytes requires PyYAML
    monkeypatch.setattr(
        importlib.resources,
        "files",
        lambda _pkg: _FakeTraversable(_TINY_GLOSSARY),
    )

    external = glossary._load_external()

    assert "FAKETERM" in external, (
        "external glossary silently dropped — the non-filesystem Traversable was "
        "not loaded (the X-1 regression)"
    )
    assert external["FAKETERM"]["definition"].startswith("A synthetic term")
    # Bare numeric YAML keys must be normalized to str, as before.
    assert "837" in external


def test_define_resolves_term_from_non_filesystem_traversable(monkeypatch):
    """End-to-end: define() surfaces a term that lives only in the external
    (zip-loaded) glossary, not in the offline fallback."""
    pytest.importorskip("yaml")
    monkeypatch.setattr(
        importlib.resources,
        "files",
        lambda _pkg: _FakeTraversable(_TINY_GLOSSARY),
    )
    entry = glossary.define("faketerm")
    assert entry is not None
    assert entry["definition"].startswith("A synthetic term")


def test_missing_resource_falls_back_to_offline(monkeypatch):
    """When the Traversable reports no file, the loader degrades to the built-in
    fallback (empty external) rather than raising."""

    class _AbsentTraversable(_FakeTraversable):
        def is_file(self) -> bool:
            return False

    monkeypatch.setattr(
        importlib.resources,
        "files",
        lambda _pkg: _AbsentTraversable(""),
    )
    assert glossary._load_external() == {}
    # Offline fallback still answers the most-asked metrics.
    assert glossary.define("PMPM") is not None


def test_bundled_glossary_loads_from_source_checkout():
    """Sanity: against the real bundled resource the full canonical glossary
    (far larger than the offline fallback) loads."""
    pytest.importorskip("yaml")
    external = glossary._load_external()
    assert len(external) > len(glossary._FALLBACK_GLOSSARY), (
        "the bundled canonical glossary should be materially larger than the "
        "offline fallback set"
    )
    assert "PMPM" in glossary.known_terms()
