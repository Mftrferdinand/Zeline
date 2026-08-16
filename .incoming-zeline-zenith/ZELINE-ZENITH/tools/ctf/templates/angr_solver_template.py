#!/usr/bin/env python3
"""
angr_solver_template.py — symbolic-execution solver skeleton for ctf-rev.

Three common entry modes. Pick the one matching how the binary takes input.
Set BINARY, FLAG_LEN, and the success/avoid conditions (prefer addresses).

    python3 angr_solver_template.py
"""
import angr
import claripy
import logging

logging.getLogger("angr").setLevel(logging.ERROR)

BINARY = "./chal"
FLAG_LEN = 32

# Prefer concrete addresses pulled from the decompiler over string matching.
FIND_ADDR = None      # e.g. 0x401234 (address that prints "Correct")
AVOID_ADDR = None     # e.g. 0x401260 (address that prints "Wrong")
SUCCESS_STR = b"Correct"
FAIL_STR = b"Wrong"


def printable(state, sym):
    for byte in sym.chop(8):
        state.solver.add(byte >= 0x20)
        state.solver.add(byte <= 0x7E)


def solve_stdin():
    proj = angr.Project(BINARY, auto_load_libs=False)
    flag = claripy.BVS("flag", 8 * FLAG_LEN)
    st = proj.factory.full_init_state(
        stdin=flag,
        add_options=angr.options.unicorn,
    )
    printable(st, flag)
    sm = proj.factory.simulation_manager(st)

    find = FIND_ADDR or (lambda s: SUCCESS_STR in s.posix.dumps(1))
    avoid = AVOID_ADDR or (lambda s: FAIL_STR in s.posix.dumps(1))
    sm.explore(find=find, avoid=avoid)

    if sm.found:
        sol = sm.found[0]
        print("[+] FLAG:", sol.posix.dumps(0))
    else:
        print("[-] no path found — try veritesting or check find/avoid")


def solve_argv():
    proj = angr.Project(BINARY, auto_load_libs=False)
    arg = claripy.BVS("arg", 8 * FLAG_LEN)
    st = proj.factory.entry_state(args=[BINARY, arg])
    printable(st, arg)
    sm = proj.factory.simulation_manager(st)
    sm.explore(
        find=FIND_ADDR or (lambda s: SUCCESS_STR in s.posix.dumps(1)),
        avoid=AVOID_ADDR or (lambda s: FAIL_STR in s.posix.dumps(1)),
    )
    if sm.found:
        print("[+] FLAG:", sm.found[0].solver.eval(arg, cast_to=bytes))
    else:
        print("[-] no path found")


def solve_blank_state_at_check(check_addr, buf_addr):
    """When main setup is messy: jump straight to the check function."""
    proj = angr.Project(BINARY, auto_load_libs=False)
    st = proj.factory.blank_state(addr=check_addr)
    flag = claripy.BVS("flag", 8 * FLAG_LEN)
    st.memory.store(buf_addr, flag)
    printable(st, flag)
    sm = proj.factory.simulation_manager(st)
    sm.explore(find=FIND_ADDR, avoid=AVOID_ADDR)
    if sm.found:
        print("[+] FLAG:", sm.found[0].solver.eval(flag, cast_to=bytes))


if __name__ == "__main__":
    # default: stdin mode. Swap to solve_argv() or the blank-state variant.
    solve_stdin()
