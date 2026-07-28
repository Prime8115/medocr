"""Local storage adapter round-trip and path-traversal safety."""
from app.services.storage import LocalStorage


def test_local_storage_roundtrip(tmp_path):
    store = LocalStorage(str(tmp_path))
    ref = store.save(b"hello bytes", "prescription.jpg", "image/jpeg")
    assert ref.startswith("local://")
    assert store.load(ref) == b"hello bytes"


def test_local_storage_strips_path_components(tmp_path):
    store = LocalStorage(str(tmp_path))
    ref = store.save(b"data", "../../evil.txt", "text/plain")
    # Ref key must not contain traversal segments.
    assert ".." not in ref
    assert store.load(ref) == b"data"


def test_distinct_saves_get_distinct_refs(tmp_path):
    store = LocalStorage(str(tmp_path))
    r1 = store.save(b"a", "f.jpg", "image/jpeg")
    r2 = store.save(b"b", "f.jpg", "image/jpeg")
    assert r1 != r2
