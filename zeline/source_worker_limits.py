"""Memory controls for a short-lived, trusted static parser worker.

Not a security sandbox: the worker runs only first-party analysis code, never
artifact code. Unsupported or denied limit setup fails closed before input read.
"""


def set_memory_limit(memory_bytes):
    import os
    if os.name != 'nt':
        import resource
        # Android reserves large nonresident address regions before parsing.
        # Bound writable data absolutely, and new address-space growth separately
        # instead of mistaking those reservations for hundreds of MiB of RAM.
        from pathlib import Path
        baseline_virtual = 0
        statm = Path('/proc/self/statm')
        if statm.exists():
            baseline_virtual = int(statm.read_text().split()[0]) * os.sysconf('SC_PAGE_SIZE')
        for which, desired in ((resource.RLIMIT_DATA, memory_bytes),
                               (resource.RLIMIT_AS, baseline_virtual + memory_bytes)):
            hard = resource.getrlimit(which)[1]
            maximum = min(desired, hard) if hard != resource.RLIM_INFINITY else desired
            resource.setrlimit(which, (maximum, maximum))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        return None
    return _windows_memory_job(memory_bytes)


def _windows_memory_job(memory_bytes):
    import ctypes as c
    from ctypes import wintypes as w

    size = c.c_size_t

    class Basic(c.Structure):
        _fields_ = [('user_time', c.c_longlong), ('job_time', c.c_longlong),
                    ('flags', w.DWORD), ('min_working_set', size), ('max_working_set', size),
                    ('active_processes', w.DWORD), ('affinity', size),
                    ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class IO(c.Structure):
        _fields_ = [(name, c.c_ulonglong) for name in
                    ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

    class Extended(c.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', size),
                    ('job_memory', size), ('peak_process_memory', size), ('peak_job_memory', size)]

    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise c.WinError(c.get_last_error())
    limits = Extended()
    limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY (committed bytes).
    limits.process_memory = memory_bytes
    if not kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)):
        error = c.get_last_error()
        kernel.CloseHandle(job)
        raise c.WinError(error)
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        error = c.get_last_error()
        kernel.CloseHandle(job)
        raise c.WinError(error)
    # Keep this kernel handle open for the worker lifetime. The OS closes it on
    # exit. No kill-on-close flag: the worker must flush its result before exit.
    return job
