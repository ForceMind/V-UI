# Local export profiling checkpoint

This WIP records a diagnostic only; it is not a product optimization or resource acceptance result. The candidate branch remains unchanged.

Run the harness with Python 3.12 and the repository test dependencies, using `--source-root` for a clean exact candidate checkout, `--expected-commit` for that 40-character commit, and an empty `--output-dir`. It creates and removes a temporary fake 1000-node database, test CA and the existing 512+512/64 routing fixture. It checks full response semantics and equal digests on repeated direct route-function calls.

The recorded 405b64a run pins only the process CPU affinity, not a cgroup memory or CPU quota. It excludes HTTP, authentication middleware, network and concurrency. Three unprofiled repeats supply wall/CPU measurements; separate cProfile calls supply all recorded function counters. cProfile itself adds overhead, so its cumulative times are not request latency. No result here proves a speedup, low-resource qualification, or a new product behavior. The source HEAD, Python/kernel/libc, affinity, full response hashes and per-function counters are in the JSON.

The large fixture invokes `_covers` 1,708,454 times in each Mihomo route plan. This supports investigating the repeated linear coverage scans; any replacement must preserve rule ordering, exact-vs-suffix boundaries, local/intranet/direct priority, DNS policies, warnings and no-DIRECT behavior. No replacement is included in this checkpoint.
