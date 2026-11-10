"""No-dependency release validator; stdout is only a bundle identity digest."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))

from app.reactionx.release_bundle import BundleError, bundle_digest, freeze_bundle


def main() -> None:
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "freeze":
            print(freeze_bundle(Path(sys.argv[2]), Path(sys.argv[3])))
        elif len(sys.argv) in (3, 4) and sys.argv[1] == "verify":
            expected = sys.argv[3] if len(sys.argv) == 4 else None
            print(bundle_digest(Path(sys.argv[2]), expected))
        else:
            raise BundleError(
                "Usage: reactionx-release-bundle.py freeze SOURCE DEST | verify ROOT [DIGEST]"
            )
    except (BundleError, OSError) as exc:
        message = (
            str(exc)
            if isinstance(exc, BundleError)
            else "Bundle filesystem operation failed"
        )
        print(message, file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
