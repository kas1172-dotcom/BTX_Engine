"""python -m monitor_engine.targets --config PATH --output DIR

Build the account-map artifact + interactive page for one client config. Reads
the config's ``account_map`` block; CSV sources resolve relative to the config's
directory. Fails fast if a configured API source needs an env var that is unset.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(prog="python -m monitor_engine.targets")
    ap.add_argument("--config", required=True, type=Path, metavar="PATH")
    ap.add_argument("--output", default=Path("output"), type=Path, metavar="DIR")
    args = ap.parse_args()

    from monitor_engine.collectors.base import check_env_vars
    from monitor_engine.models import ClientConfig
    from monitor_engine.targets.build import build_map_data, write_map_site

    config = ClientConfig.model_validate(json.loads(args.config.read_text(encoding="utf-8")))
    if config.account_map is None:
        print("[NO ACCOUNT MAP] config has no account_map block; nothing to build.",
              file=sys.stderr)
        sys.exit(1)
    try:
        check_env_vars(config)
    except EnvironmentError as exc:
        print(f"\n[MISSING ENV VARS]\n{exc}", file=sys.stderr)
        sys.exit(1)

    map_data = build_map_data(config, base_dir=args.config.parent)
    write_map_site(map_data, args.output)
    print(
        f"Account map: {len(map_data.targets)} account(s), "
        f"{map_data.placed_count} placed → {args.output / 'map.html'}"
    )


if __name__ == "__main__":
    main()
