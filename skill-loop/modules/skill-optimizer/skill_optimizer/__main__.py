"""C 线 CLI。

    python -m skill_optimizer propose --run-bundle <dir> --out <dir>
    python -m skill_optimizer regress --baseline <json> --candidate <json> --out <md>
    python -m skill_optimizer regress --baseline <json> --no-candidate --out <md>
    python -m skill_optimizer validate --schema <schema.json> --data <file>
"""

from __future__ import annotations

import argparse
import sys

from . import contracts
from . import propose as propose_mod
from . import regress as regress_mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m skill_optimizer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_prop = sub.add_parser("propose")
    p_prop.add_argument("--run-bundle", required=True)
    p_prop.add_argument("--out", required=True)
    p_prop.add_argument("--config", default=None)

    p_reg = sub.add_parser("regress")
    p_reg.add_argument("--baseline", required=True)
    p_reg.add_argument("--candidate", default=None)
    p_reg.add_argument("--no-candidate", action="store_true")
    p_reg.add_argument("--out", required=True)
    p_reg.add_argument("--config", default=None)

    p_val = sub.add_parser("validate")
    p_val.add_argument("--schema", required=True)
    p_val.add_argument("--data", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "propose":
        return propose_mod.propose(args.run_bundle, args.out, args.config)
    if args.cmd == "regress":
        return regress_mod.regress(
            args.baseline, args.out, candidate=args.candidate, no_candidate=args.no_candidate, config_path=args.config
        )
    if args.cmd == "validate":
        return contracts.main(["--schema", args.schema, "--data", args.data])
    return 2


if __name__ == "__main__":
    sys.exit(main())
