from __future__ import annotations

import sys


def main() -> int:
    # Hermes 0.17 puts site-packages/plugins before site-packages while starting
    # the gateway. That shadows the real top-level cron package with
    # plugins/cron, so gateway/run.py later cannot import cron.scheduler_provider.
    # Preloading the real cron package pins it in sys.modules before Hermes
    # mutates sys.path.
    import cron
    import cron.jobs  # noqa: F401
    import cron.scheduler_provider  # noqa: F401

    print(f"[HERMES] using cron package: {getattr(cron, '__file__', '')}", flush=True)

    from hermes_cli.main import main as hermes_main

    sys.argv = ["hermes", "gateway"]
    return int(hermes_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
