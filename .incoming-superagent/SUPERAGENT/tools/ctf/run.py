#!/usr/bin/env python3
"""
run.py — entrypoint for the CTF coordinator.

  python3 run.py                # start the poll/solve loop
  python3 run.py approve        # submit all HITL-queued flags (operator action)
  python3 run.py status         # print current state summary

Configure via .env (see .env.example). Build the sandbox image first:
  docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
"""
import sys
from coordinator import Coordinator


def main():
    coord = Coordinator()
    arg = sys.argv[1] if len(sys.argv) > 1 else "run"
    if arg == "run":
        coord.run()
    elif arg == "approve":
        coord.approve_pending()
    elif arg == "status":
        for cs in coord.state.challenges.values():
            print(f"[{cs.status:>18}] {cs.id:>4} {cs.name}  "
                  f"flags={cs.flags_found or '-'}")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
