"""Set CPU, file-size and open-file limits, then exec the given command.

Used by the sandbox instead of preexec_fn, which is unsafe in threaded programs.
"""

import os
import resource
import sys


def main() -> None:
    cpu, fsize_mb, nofile = (int(x) for x in sys.argv[1:4])
    if sys.argv[4] != "--":
        raise SystemExit("usage: rlimit_exec.py CPU FSIZE_MB NOFILE -- cmd ...")
    cmd = sys.argv[5:]
    for limit, value in (
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_FSIZE, fsize_mb * 1024 * 1024),
        (resource.RLIMIT_NOFILE, nofile),
    ):
        _soft, hard = resource.getrlimit(limit)
        cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(limit, (cap, hard))
    os.execvp(cmd[0], cmd)  # nosec B606 - argv built by Forge's sandbox, not by a model


if __name__ == "__main__":
    main()
