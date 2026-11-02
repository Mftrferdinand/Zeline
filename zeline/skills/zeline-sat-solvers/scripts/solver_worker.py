#!/usr/bin/env python3
"""Minimal fresh-process SAT resource guard, with no framework imports.

The process PID/group and resource ceilings survive exec into the native solver.
Darwin uses the same bounded post-startup VM-growth policy as native workers.
"""
import os
import sys

MAX_OUTPUT_BYTES = 16 * 1024 * 1024


def _darwin_virtual_size():
    """Read the current task VM reservation, not RSS, through Apple's libproc ABI."""
    import ctypes as c
    import os

    # xnu/bsd/sys/proc_info.h: PROC_PIDTASKINFO = 4, struct proc_taskinfo.
    class TaskInfo(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in
                    ('pti_virtual_size', 'pti_resident_size', 'pti_total_user',
                     'pti_total_system', 'pti_threads_user', 'pti_threads_system')]
        _fields_ += [(name, c.c_int32) for name in
                     ('pti_policy', 'pti_faults', 'pti_pageins', 'pti_cow_faults',
                      'pti_messages_sent', 'pti_messages_received', 'pti_syscalls_mach',
                      'pti_syscalls_unix', 'pti_csw', 'pti_threadnum',
                      'pti_numrunning', 'pti_priority')]

    library = c.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    library.proc_pidinfo.argtypes = [c.c_int, c.c_int, c.c_uint64, c.c_void_p, c.c_int]
    library.proc_pidinfo.restype = c.c_int
    info = TaskInfo()
    received = library.proc_pidinfo(os.getpid(), 4, 0, c.byref(info), c.sizeof(info))
    if received != c.sizeof(info) or not info.pti_virtual_size:
        raise OSError(c.get_errno(), 'cannot measure Darwin worker virtual-memory baseline')
    return int(info.pti_virtual_size)


def child_limits(output_bytes=MAX_OUTPUT_BYTES):
    """POSIX child-only limits; never change the agent process's resources."""
    import resource
    # Match the existing native-worker policy: XNU DATA limits include startup
    # VM reservations. Budget new VM growth, not those nonresident mappings.
    baseline = _darwin_virtual_size() if sys.platform == 'darwin' else 0
    for label, kind, bound in (('FSIZE', resource.RLIMIT_FSIZE, output_bytes),
                               ('DATA', resource.RLIMIT_DATA, baseline + 2 * 1024 ** 3),
                               ('CORE', resource.RLIMIT_CORE, 0)):
        try:
            soft, hard = resource.getrlimit(kind)
            # Never attempt to increase an inherited tighter soft ceiling. On
            # Darwin the finite kernel DATA maximum may differ from hard=inf.
            value = min([bound] + [limit for limit in (soft, hard)
                                   if limit != resource.RLIM_INFINITY])
            resource.setrlimit(kind, (value, value))
        except (OSError, ValueError) as exc:
            # Expose only a safe limit identifier/type/errno, never process data.
            os.write(2, ('ZELINE_LIMIT_SETUP ' + label + ' ' + type(exc).__name__
                         + ' errno=' + str(getattr(exc, 'errno', None)) + '\n').encode())
            raise


def main():
    if len(sys.argv) < 3:
        raise SystemExit('missing output ceiling or solver executable')
    try:
        output_bytes = int(sys.argv[1])
        if not 0 < output_bytes <= MAX_OUTPUT_BYTES:
            raise ValueError('invalid output ceiling')
        child_limits(output_bytes)
    except (OSError, ValueError) as exc:
        os.write(2, ('ZELINE_LIMIT_SETUP PLATFORM ' + type(exc).__name__ +
                     ' errno=' + str(getattr(exc, 'errno', None)) + '\n').encode())
        raise SystemExit(125)
    try:
        os.execv(sys.argv[2], sys.argv[2:])
    except OSError as exc:
        os.write(2, ('ZELINE_EXEC_SETUP OSError errno=' + str(exc.errno) + '\n').encode())
        raise SystemExit(126)


if __name__ == '__main__':
    main()
