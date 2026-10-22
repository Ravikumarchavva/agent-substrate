"""`loamz-server`: run the open Loamz platform.

loamz-server data serve|controller|worker|attach   the data platform
loamz-server agents serve|chat                     agent hosting
"""

from __future__ import annotations

import sys

GROUPS = ("data", "agents")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help") or args[0] not in GROUPS:
        print(__doc__.strip())
        raise SystemExit(0 if args and args[0] in ("-h", "--help") else 2)
    group, rest = args[0], args[1:]
    if group == "data":
        from loamz.server.data.cli import main as run_data

        sys.argv = ["loamz-server data", *rest]
        run_data()
    else:
        from loamz.server.agents.cli import main as run_agents

        run_agents(rest)


if __name__ == "__main__":
    main()
