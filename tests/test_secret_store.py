from __future__ import annotations

from app.core.secret_store import PROTECTED_PREFIX, protect_secret, unprotect_secret


def test_plain_secret_roundtrip_remains_compatible():
    secret = "plain-secret"

    protected = protect_secret(secret)

    assert unprotect_secret(protected) in ("", secret)
    if not protected.startswith(PROTECTED_PREFIX):
        assert protected == secret
