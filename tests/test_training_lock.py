from cloudlab.training_lock import acquire, active, release


def test_model_preparation_is_exclusive_and_releases_cleanly(tmp_path):
    first = acquire(tmp_path)
    assert first is not None
    try:
        assert active(tmp_path)
        assert acquire(tmp_path) is None
    finally:
        release(first)
    assert not active(tmp_path)
    second = acquire(tmp_path)
    assert second is not None
    release(second)
