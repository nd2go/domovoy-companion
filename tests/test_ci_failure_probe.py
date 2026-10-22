"""Temporary CI01 acceptance probe; removed after the observed red run."""


def test_required_must_reject_a_failed_test():
    raise AssertionError("Intentional CI01 failure: verify Required turns red")
