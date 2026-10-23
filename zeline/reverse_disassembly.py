"""Bounded linear disassembly of inert bytes in a temporary native worker.

No target is executed or imported. CFG observations are page-local, not functions.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

MAX_BYTES = 4096
MAX_INSTRUCTIONS = 100
MEMORY_BYTES = 128 * 1024 * 1024
TIMEOUT_SECONDS = 5.0
MAX_REQUEST_BYTES = 20 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
UINT64_MAX = (1 << 64) - 1
_WORKER_LOCK = threading.Lock()
_LIMITS_READY = False


def _validate(data, architecture, address, file_offset, limit, endianness):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError('data must be bytes, at most 4096 bytes; not truncated')
    if not isinstance(architecture, str) or architecture not in ('x86', 'x86_64', 'arm', 'thumb', 'aarch64'):
        raise ValueError('unsupported architecture')
    if endianness not in ('little', 'big'):
        raise ValueError('unsupported endianness')
    if architecture in ('x86', 'x86_64') and endianness != 'little':
        raise ValueError('x86 supports little endianness only')
    for name, value in (('address', address), ('file_offset', file_offset)):
        if type(value) is not int or not 0 <= value <= UINT64_MAX:
            raise ValueError(name + ' must be an unsigned 64-bit integer')
        if data and value + len(data) - 1 > UINT64_MAX:
            raise ValueError(name + ' byte window exceeds unsigned 64-bit bounds')
    if architecture in ('x86', 'arm', 'thumb') and (address >= 1 << 32 or len(data) > (1 << 32) - address):
        raise ValueError('decoder address window exceeds 32-bit architecture')
    if type(limit) is not int or not 1 <= limit <= MAX_INSTRUCTIONS:
        raise ValueError('limit must be an integer in 1..100')


def decode_instructions(data: bytes, architecture: str, *, address=0, file_offset=0,
                        limit=50, endianness='little', should_stop=None) -> dict:
    """Decode a single bounded page; queue/startup/decode share a five-second budget."""
    _validate(data, architecture, address, file_offset, limit, endianness)
    if should_stop is not None and not callable(should_stop):
        raise ValueError('should_stop must be callable')
    deadline = time.monotonic() + TIMEOUT_SECONDS

    def check():
        if should_stop is not None and should_stop():
            raise InterruptedError('disassembly cancelled')
        if time.monotonic() >= deadline:
            raise ValueError('disassembly time limit exceeded')

    check()
    while not _WORKER_LOCK.acquire(timeout=0.05):
        check()
    process = None
    try:
        check()
        payload = json.dumps(dict(data=base64.b64encode(data).decode('ascii'),
                                  architecture=architecture, address=address,
                                  file_offset=file_offset, limit=limit,
                                  endianness=endianness)).encode('ascii')
        from .source_worker_limits import worker_environment
        process = subprocess.Popen(
            [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            env=worker_environment())
        pending = payload
        while True:
            check()
            try:
                output, _ = process.communicate(
                    input=pending, timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                pending = None
        check()
        if process.returncode or len(output) > MAX_RESPONSE_BYTES:
            raise ValueError('disassembly resource limit or worker failure')
        try:
            response = json.loads(output)
            if not isinstance(response, dict):
                raise ValueError('invalid response')
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid disassembly worker response') from exc
        if not response.get('ok'):
            error = str(response.get('error', 'disassembly worker failure'))[:400]
            if response.get('kind') == 'RuntimeError':
                raise RuntimeError(error)
            raise ValueError(error)
        result = response['result']
        check()
        return result
    finally:
        try:
            if process is not None:
                try:
                    if process.poll() is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    process.communicate()
                finally:
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        if pipe is not None:
                            pipe.close()
        finally:
            _WORKER_LOCK.release()


def _decode_worker(request):
    # This private path is reachable only after the trusted worker applies limits.
    if __name__ != '__main__' or not _LIMITS_READY:
        raise RuntimeError('native decoding is restricted to the limited worker')
    import capstone as cs

    data = base64.b64decode(request['data'], validate=True)
    architecture = request['architecture']
    address, offset = request['address'], request['file_offset']
    limit, endian = request['limit'], request['endianness']
    _validate(data, architecture, address, offset, limit, endian)
    arch, mode = {
        'x86': (cs.CS_ARCH_X86, cs.CS_MODE_32),
        'x86_64': (cs.CS_ARCH_X86, cs.CS_MODE_64),
        'arm': (cs.CS_ARCH_ARM, cs.CS_MODE_ARM),
        'thumb': (cs.CS_ARCH_ARM, cs.CS_MODE_THUMB),
        'aarch64': (cs.CS_ARCH_ARM64, cs.CS_MODE_ARM),
    }[architecture]
    mode |= cs.CS_MODE_BIG_ENDIAN if endian == 'big' else cs.CS_MODE_LITTLE_ENDIAN
    decoder = cs.Cs(arch, mode)
    decoder.detail = True
    decoder.skipdata = False
    instructions = []
    for insn in decoder.disasm(data, address, count=limit):
        flow = _flow(cs, insn, architecture)
        instructions.append(dict(address=insn.address, file_offset=offset + insn.address - address,
                                 size=insn.size, bytes_hex=bytes(insn.bytes).hex(),
                                 mnemonic=insn.mnemonic, op_str=insn.op_str, flow=flow))
    consumed = sum(item['size'] for item in instructions)
    stop = 'end' if consumed == len(data) else 'limit' if len(instructions) == limit else 'undecodable'
    xrefs = [dict(source=i['address'], target=i['flow']['target'], kind=i['flow']['kind'])
             for i in instructions if i['flow']['kind'] in ('call', 'jump')]
    return dict(decoder=dict(name="capstone", package_version=cs.__version__, api_version=list(cs.cs_version()[:2])),
                architecture=architecture, endianness=endian, address=address,
                file_offset=offset, bytes_supplied=len(data), bytes_consumed=consumed,
                instructions=instructions, xrefs=xrefs, blocks=_blocks(instructions),
                stop_reason=stop, limitations=[
                    'Linear decode only; no skipdata, overlapping alternatives, decompilation or call graph.',
                    'CFG and xrefs are page-local observations, not whole-function control flow.',
                    'Indirect targets unresolved; call fallthrough is an observation, not proof of return.',
                    'Fallthrough outside the ISA address width is omitted; address wrap is not inferred.',
                    'UD2 is an unresolved trap; exception handlers and other exceptional transfers are not fully modeled.',
                    'Serialized memory/time-limited temporary worker; not an OS sandbox.'])


def _flow(cs, insn, architecture):
    kind, target, conditional = 'linear', None, None
    if insn.group(cs.CS_GRP_RET) or insn.group(cs.CS_GRP_IRET):
        kind = 'return'
    elif insn.group(cs.CS_GRP_CALL):
        kind = 'call'
    elif insn.group(cs.CS_GRP_JUMP):
        kind = 'jump'
    if architecture in ('x86', 'x86_64'):
        from capstone.x86 import X86_INS_UD2
        if insn.id == X86_INS_UD2:
            kind, conditional = 'trap', False
    if architecture in ('arm', 'thumb'):
        from capstone.arm import ARM_INS_BX, ARM_INS_MOV, ARM_INS_POP, ARM_REG_LR, ARM_REG_PC
        operands = insn.operands
        if kind == 'linear' and ARM_REG_PC in insn.regs_access()[1]:
            kind = 'jump'  # Computed PC writes terminate flow without resolving a target.
        if ((insn.id == ARM_INS_BX and operands and operands[0].reg == ARM_REG_LR)
                or (insn.id == ARM_INS_POP and any(op.type == cs.CS_OP_REG and op.reg == ARM_REG_PC for op in operands))
                or (insn.id == ARM_INS_MOV and len(operands) == 2
                    and operands[0].type == cs.CS_OP_REG and operands[0].reg == ARM_REG_PC
                    and operands[1].type == cs.CS_OP_REG and operands[1].reg == ARM_REG_LR)):
            kind = 'return'
    if kind in ('call', 'jump', 'return'):
        # Last operand is the branch destination (CBZ/TBZ have earlier immediates).
        operands = insn.operands
        direct = bool(operands and operands[-1].type == cs.CS_OP_IMM)
        if architecture in ('x86', 'x86_64'):
            direct = direct and len(operands) == 1  # Far selector:offset needs descriptor state.
        elif architecture in ('arm', 'thumb'):
            from capstone.arm import ARM_INS_B, ARM_INS_BL, ARM_INS_BLX, ARM_INS_CBZ, ARM_INS_CBNZ
            direct = direct and insn.id in (ARM_INS_B, ARM_INS_BL, ARM_INS_BLX, ARM_INS_CBZ, ARM_INS_CBNZ)
        if kind != 'return' and direct:
            width = 32 if architecture in ('x86', 'arm', 'thumb') else 64
            target = operands[-1].imm & ((1 << width) - 1)
        if architecture in ('x86', 'x86_64'):
            from capstone.x86 import X86_INS_JMP, X86_INS_LJMP
            conditional = kind == 'jump' and insn.id not in (X86_INS_JMP, X86_INS_LJMP)
        elif architecture in ('arm', 'thumb'):
            from capstone.arm import ARM_CC_AL, ARM_CC_INVALID
            conditional = insn.cc not in (ARM_CC_AL, ARM_CC_INVALID) or insn.mnemonic.startswith(('cbz', 'cbnz'))
        else:
            from capstone.arm64 import (ARM64_CC_AL, ARM64_CC_NV, ARM64_CC_INVALID,
                                        ARM64_INS_CBZ, ARM64_INS_CBNZ, ARM64_INS_TBZ, ARM64_INS_TBNZ)
            conditional = (insn.cc not in (ARM64_CC_AL, ARM64_CC_NV, ARM64_CC_INVALID)
                           or insn.id in (ARM64_INS_CBZ, ARM64_INS_CBNZ, ARM64_INS_TBZ, ARM64_INS_TBNZ))
    fallthrough = insn.address + insn.size
    address_max = (1 << (32 if architecture in ('x86', 'arm', 'thumb') else 64)) - 1
    if fallthrough > address_max or (kind in ('return', 'jump', 'trap') and not conditional):
        fallthrough = None
    flow = dict(kind=kind, target=target, conditional=conditional, fallthrough=fallthrough)
    if architecture in ('x86', 'x86_64') and kind in ('call', 'jump') and target is None:
        from capstone.x86 import X86_INS_CALL, X86_INS_JMP, X86_REG_RIP
        width = 4 if architecture == 'x86' else 8
        # Capstone can label FF /3 far memory calls as CALL with pointer-size
        # operands too. Only FF /2 and FF /4 denote the near slot transfers.
        near_slot = insn.opcode[0] == 0xff and (insn.modrm >> 3) & 7 == (2 if kind == 'call' else 4)
        if near_slot and insn.id in (X86_INS_CALL, X86_INS_JMP) and len(insn.operands) == 1:
            operand = insn.operands[0]
            if operand.type == cs.CS_OP_MEM and operand.size == width and insn.addr_size == width:
                memory = operand.mem
                if not memory.segment and not memory.index:
                    slot, basis = None, None
                    if architecture == 'x86_64' and memory.base == X86_REG_RIP:
                        slot, basis = insn.address + insn.size + memory.disp, 'rip_relative'
                    elif not memory.base:
                        slot, basis = memory.disp, 'absolute'
                    if slot is not None:
                        flow['memory_reference'] = dict(address=slot & ((1 << (width * 8)) - 1),
                                                        width=width, basis=basis)
    return flow


def _blocks(instructions):
    if not instructions:
        return []
    addresses = {item['address'] for item in instructions}
    leaders = {instructions[0]['address']}
    for index, item in enumerate(instructions):
        flow = item['flow']
        if flow['kind'] != 'linear':
            if flow['target'] in addresses:
                leaders.add(flow['target'])
            if index + 1 < len(instructions):
                leaders.add(instructions[index + 1]['address'])
    blocks = []
    for item in instructions:
        if item['address'] in leaders:
            blocks.append(dict(address=item['address'], end_address=None, instructions=[], edges=[]))
        blocks[-1]['instructions'].append(item['address'])
        blocks[-1]['end_address'] = item['address'] + item['size']
    lookup = {item['address']: item for item in instructions}
    for block in blocks:
        last = lookup[block['instructions'][-1]]
        flow = last['flow']

        def edge(kind, target):
            block['edges'].append(dict(kind=kind, target=target,
                                       scope='unresolved' if target is None else
                                       'local' if target in addresses else 'external'))

        if flow['kind'] != 'linear':
            edge(flow['kind'], flow['target'])
        if flow['fallthrough'] is not None:
            edge('fallthrough', flow['fallthrough'])
    return blocks


def _worker_main():
    import faulthandler
    import traceback  # Trusted stdlib startup; diagnostics must not import after OOM.
    faulthandler.enable()
    global _LIMITS_READY
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        '_zeline_disassembly_limits', Path(__file__).with_name('source_worker_limits.py'))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    try:
        job = helper.set_memory_limit(MEMORY_BYTES)
        _LIMITS_READY = True
    except Exception:
        print(json.dumps(dict(ok=False, kind='RuntimeError',
                              error='disassembly memory isolation unavailable; no unsafe fallback')))
        return
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError('disassembly worker request limit exceeded')
        response = dict(ok=True, result=_decode_worker(json.loads(raw)))
    except Exception as exc:
        if isinstance(exc, MemoryError):
            traceback.print_exc(limit=6)
        response = dict(ok=False, kind=type(exc).__name__, error=str(exc)[:400])
    output = json.dumps(response, ensure_ascii=True)
    if len(output) > MAX_RESPONSE_BYTES:
        output = json.dumps(dict(ok=False, error='disassembly response limit exceeded'))
    print(output)
    _ = job


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('internal disassembly worker only')
    _worker_main()
