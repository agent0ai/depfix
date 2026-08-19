# Depfix I/O concurrency proposal

Status: measured design and implementation evidence for the unreleased Depfix source after 0.11.4.

## Decision

Add a narrow synchronous worker pool around exact wheel acquisition and inspection after uv has produced a grouped plan.
Use four workers as the initial default and `1` as the rollback setting. Keep graph mutation, wheel extraction and RECORD
validation, installed-target verification, source builds, manifest/store persistence, local preparation, and activation
ordered.

Four is the 80/20 bound supported by the connected live workload. For 26 exact public wheels, acquisition and inspection
fell from a 4.777-second median at one worker to 1.861 seconds at four (2.57x). Eight reached 1.584 seconds (3.02x), but
used 63% more CPU than four for only another 0.277 seconds. On the 13-wheel partial set, four reached 1.446 seconds and
eight regressed to 1.500 seconds. Two tiny wheels saturated at two workers. Four therefore captures most observed gain
without making eight simultaneous streams, temporary files, and locks the default.

The same bound also fits metadata: exact Simple-index queries for all 26 distributions fell from a 5.375-second median
serially to 2.418 seconds at four (2.22x). Eight regressed to a 2.792-second median and had one 30.726-second tail run.
Use one shared four-worker budget for metadata and artifacts, rather than two pools that could create eight active opens.

Do not apply the same pool to local work. Four-way materialization was 0.615 seconds versus 0.463 serial, and four-way
warm verification was 0.160 seconds versus 0.083 serial. The proposal is not an `asyncio` rewrite and does not put another
executor around uv, which already owns concurrency inside its single planning subprocess.

## Reproducible evidence

The retained probe is [`benchmarks/io_concurrency.py`](../../benchmarks/io_concurrency.py), the Agent Zero input generator
is [`benchmarks/generate_io_concurrency_trace.py`](../../benchmarks/generate_io_concurrency_trace.py), its exact medium input is
[`benchmarks/data/io-concurrency-medium.json`](../../benchmarks/data/io-concurrency-medium.json), and all 107 per-run
observations are in [`io-concurrency-evidence.json`](io-concurrency-evidence.json). Regenerate the Agent Zero trace from
the retained exact requirements snapshot, then run the matrix from the repository root:

```bash
.venv/bin/python benchmarks/generate_io_concurrency_trace.py \
  --requirements benchmarks/data/io-concurrency-agent-zero-requirements.txt \
  --uv .venv/bin/uv --output benchmarks/data/io-concurrency-agent-zero-summary.json
PYTHONPATH=/path/to/clean-v0.11.4/src .venv/bin/python benchmarks/io_concurrency.py \
  --trace benchmarks/data/io-concurrency-medium.json \
  --agent-zero-trace benchmarks/data/io-concurrency-agent-zero-summary.json --uv .venv/bin/uv \
  --workers 1 2 4 8 --repetitions 3 \
  --baseline-label 'Depfix v0.11.4 commit c83d4f9a...' \
  --output docs/research/io-concurrency-evidence.json
```

Every measured observation runs in a fresh process and task-owned cache. Partial and warm end-to-end state is prepared in
a separate process before the measured process, so `ru_maxrss` is state-specific rather than inherited from cold setup.
Acquisition calls shipped `Cache.fetch_url()` and `inspect_wheel()`, then rechecks every size and SHA-256. Materialization
calls shipped `extract_wheel()` and validates every promoted target with `Cache.has_package()`. Warm verification repeats
complete file/hash validation. Worker order alternates by repetition. Temporary caches are automatically removed.

The harness wraps Depfix's actual `_open_url()` calls, redirect callbacks, artifact-lock entries, and subprocess boundary.
`transport_open_calls` therefore counts initial and retry opens; `redirect_requests` separately counts redirects. The
summed open/read wait can exceed wall time when worker waits overlap. Lock wait is recorded only when a lock directory was
already present at entry. Self and waited-child CPU, subprocess wall, parent/child peak RSS, and parent/child kernel block
counters are recorded separately. uv's internal HTTP exchanges remain opaque and are never counted as Depfix requests.

The code baseline is clean `v0.11.4` commit `c83d4f9a5fa1809310bd6a2903a39f35e30544ac`, exported outside the live worktree.
This matters because unrelated product edits appeared concurrently during this analysis; none is included in the evidence
or this proposal. The host was Linux AArch64, CPython 3.11.2, six logical CPUs, 5.8 GiB RAM, overlay storage, and public
PyPI/CDN networking. Repository HEAD differs from the release only by release documentation in the committed baseline.

### Workloads and states

| Workload/state | Exact boundary | Artifacts / bytes | Repetitions |
| --- | --- | ---: | ---: |
| Small cold | Exact `idna` and `six` wheel acquire + inspect | 2 / 79,600 | 3 per bound |
| Medium cold | Connected six-root uv resolution retained as exact wheels | 26 / 18,599,770 | 3 per bound and 3 end-to-end |
| Medium partial | Sorted alternating half of exact wheels/targets absent | 13 / 16,749,519 | 3 per bound and 3 end-to-end |
| Medium warm | Complete immutable targets | 26 / 18,599,770 source bytes | 3 per bound and 3 end-to-end |
| Metadata cold | Exact Simple-index payload for every medium distribution | 26 records | 3 per bound |
| Agent Zero replay | 32 equal wheel-size strata through p95 | 32 / 61,207,328 | 3 per bound |
| Agent Zero large | Exact-plan three largest wheels | 3 / 1,414,622,583 | 1 at bounds 1 and 4 |
| Agent Zero source | Smallest, median, and largest source-only sdists | 3 / 7,452,844 | 3 per bound |

The medium roots are Requests, Flask, Pydantic, Boto3, Markdown, and Beautiful Soup. They produce a connected graph with
pure wheels plus native MarkupSafe, charset-normalizer, and pydantic-core wheels. The partial selection is intentionally
deterministic, includes the dominant Botocore wheel, and models an exact manifest whose missing targets need reacquisition.

The Agent Zero snapshot is commit `baadd0dd0b09fa769a1027c183b964be85d5c8cc`; its exact requirements and reproducible
generator are retained alongside the generated trace in
[`benchmarks/data/io-concurrency-agent-zero-summary.json`](../../benchmarks/data/io-concurrency-agent-zero-summary.json).
The generator verifies requirements SHA-256 `14ad1b...c0b8bd`, runs uv 0.12.5 for CPython 3.11 Linux AArch64, and selects
the best compatible wheel using packaging tag rank. The plan contains 327 distributions: 318 wheel-bearing, nine
source-only, and 3,386,878,041 selected wheel bytes. The replay retains the complete 318-value size distribution, samples
32 equal strata through p95, separately exercises the three largest exact wheels (1.415 GB), and builds the smallest,
median, and largest source-only sdists. This preserves artifact-count and size/build distribution while bounding transfer
to 61.2 MB for repeated trials and 1.415 GB for the one/four-worker large trial. It is tied to the exact plan but not
topology-aware because PEP 751 pylock is flat and has no dependency edges. A full connected prepare remains unsafe on the
5.8 GiB host with occupied swap and 3.39 GB of wheels, so no Agent Zero end-to-end speedup is claimed. The exact source-only
cohort contains legacy Python builds, not a native/compiler project; native requirements in this plan have compatible
wheels, so a compiler-build timing would be a synthetic workload rather than faithful Agent Zero evidence.

### End-to-end baselines

Ranges are three isolated measured processes. Transport wait is summed exact open/read elapsed time, not wall-time
subtraction. Output blocks are `ru_oublock`; Linux commonly reports 512-byte units, but overlay storage and page cache make
physical bytes and disk-only wait unavailable. The serial materialize wall-minus-CPU upper bound is 0–0.005 seconds and
warm verify is 0–0.001 seconds, so no material local disk wait was observed.

| State | Wall s | Self CPU s | Child CPU s | Opens / redirects | Transport wait sum s | Subprocesses / wall s | Parent / child RSS MiB | Parent / child output blocks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cold | 10.990–15.925 | 2.280–2.621 | 0.203–0.207 | 52 / 0 | 9.548–14.412 | 2 / 0.615–0.643 | 84.8–96.4 / 74.4–75.6 | 114,256 / 16,088 |
| Partial | 2.118–3.003 | 0.681–0.724 | 0 | 13 / 0 | 1.696–2.553 | 0 / 0 | 44.0 / 0 | 16,568 / 0 |
| Warm | 0.330–0.341 | 0.329–0.340 | 0 | 0 / 0 | 0 | 0 / 0 | 44.0 / 0 | 880 / 0 |

Cold performs one uv validation plus one uv plan subprocess; its internal request count and split between subprocess CPU,
network, and scheduling wait are unknown. The 52 observed Depfix opens are 26 metadata plus 26 artifact calls. Partial's
13 calls are exact artifact repairs, and warm performs none. All 107 observations had zero redirect callbacks and zero
contended lock entries; retry paths were instrumented but not exercised.

### Bounded candidate experiments

Wall ranges are three fresh-process runs; speedup uses the median at one worker as the denominator.

| Phase/state | Workers | Wall range s | Median s / speedup | Self CPU range s | Transport wait sum range s | Peak RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Simple metadata medium | 1 / 2 / 4 / 8 | 5.180–5.607 / 3.029–3.412 / 2.271–2.509 / 2.179–30.726 | 5.375 / 1.00x; 3.036 / 1.77x; 2.418 / 2.22x; 2.792 / 1.93x | 0.919–0.954 / 0.819–0.870 / 0.961–0.991 / 1.493–1.885 | 5.099–5.521 / 5.945–6.690 / 7.006–7.482 / 10.254–97.936 | 84.0–124.2 |
| Acquire small | 1 / 2 / 4 / 8 | 0.218–0.238 / 0.140–0.166 / 0.139–0.148 / 0.136–0.142 | 0.225 / 1.00x; 0.149 / 1.51x; 0.148 / 1.52x; 0.141 / 1.60x | 0.039–0.041 / 0.055–0.058 / 0.056–0.058 / 0.054–0.059 | 0.212–0.233 / 0.245–0.263 / 0.239–0.254 / 0.233–0.241 | 44.0 |
| Acquire medium cold | 1 / 2 / 4 / 8 | 4.583–4.843 / 2.540–2.649 / 1.789–1.903 / 1.581–1.618 | 4.777 / 1.00x; 2.598 / 1.84x; 1.861 / 2.57x; 1.584 / 3.02x | 0.848–0.863 / 0.743–0.860 / 0.901–0.954 / 1.402–1.644 | 4.434–4.677 / 4.855–5.090 / 6.741–7.033 / 8.165–9.040 | 44.0–46.2 |
| Acquire medium partial | 1 / 2 / 4 / 8 | 2.804–2.835 / 1.702–1.735 / 1.435–1.475 / 1.474–1.504 | 2.825 / 1.00x; 1.703 / 1.66x; 1.446 / 1.95x; 1.500 / 1.88x | 0.482–0.512 / 0.491–0.508 / 0.605–0.618 / 1.087–1.153 | 2.705–2.731 / 3.213–3.252 / 4.207–4.617 / 4.390–4.608 | 44.0–44.4 |
| Agent Zero 32-stratum sample | 1 / 2 / 4 / 8 | 8.392–8.895 / 5.839–6.141 / 4.902–4.961 / 4.735–5.015 | 8.411 / 1.00x; 5.874 / 1.43x; 4.952 / 1.70x; 4.767 / 1.76x | 1.436–1.446 / 1.376–1.532 / 1.568–1.726 / 2.100–2.498 | 8.114–8.612 / 10.106–11.041 / 12.085–14.150 / 16.554–17.579 | 44.0–50.9 |
| Agent Zero three largest | 1 / 4 | 97.529 / 108.298 | 97.529 / 1.00x; 108.298 / 0.90x | 16.902 / 21.819 | 94.759 / 303.065 | 45.2 / 49.5 |
| Materialize medium | 1 / 2 / 4 / 8 | 0.460–0.535 / 0.416–0.453 / 0.612–0.631 / 0.702–0.735 | 0.463 / 1.00x; 0.419 / 1.11x; 0.615 / 0.75x; 0.717 / 0.65x | 0.459–0.533 / 0.557–0.609 / 0.860–0.885 / 1.115–1.159 | 0 | 44.0–48.0 |
| Warm verify medium | 1 / 2 / 4 / 8 | 0.081–0.085 / 0.095–0.096 / 0.159–0.161 / 0.172–0.176 | 0.083 / 1.00x; 0.096 / 0.86x; 0.160 / 0.52x; 0.174 / 0.48x | 0.081–0.085 / 0.121–0.122 / 0.211–0.216 / 0.268–0.275 | 0 | 44.0–45.6 |
| Agent Zero three-source build | 1 / 2 / 4 / 8 | 3.369–3.430 / 2.675–2.718 / 1.957–2.013 / 1.923–2.120 | 3.394 / 1.00x; 2.676 / 1.27x; 1.977 / 1.72x; 1.925 / 1.76x | self 0.137–0.203; child 1.614–1.739 | transport 0.828–1.128; subprocess sum 2.507–3.224 | parent 44.0 / child 41.0–41.7 |

Every metadata and acquisition run observed exactly one `_open_url()` call and no retry or redirect per listed item, while
verifying exact metadata identities or distinct artifact sizes, hashes, and wheel identities. This is an observed result,
not an assumption about future retries. Local phases made zero transport calls and subprocesses. The three largest wheels
wrote 2,762,992 blocks at both bounds; concurrency changed scheduling, not volume. Medium materialization wrote 77,448
blocks at every bound. Each source run made three observed Depfix opens and three real uv build subprocesses, then
validated every resulting wheel identity.

## Pipeline and critical path

For grouped `install_packages()`, a package-install identity lock spans resolve, synchronization, manifests, and install
recording to suppress duplicate same-request work:

1. Normalize roots/policy and reuse an exact manifest when available.
2. On cold/refresh, `UvBackend.resolve_requirements_plan()` starts one uv process. uv concurrently performs its own
   planning/index work in a private temporary cache and returns a complete version plan.
3. `Resolver.resolve()` walks declarations and dependency edges in stable order. `_project_artifact_payload()` performs
   blocking Simple/JSON metadata I/O; `_resolve_candidate()` downloads, hashes, and inspects one selected artifact before
   walking its dependencies. This Depfix-owned remote path is sequential.
4. Stable-sort graph state and calculate graph identity.
5. `sync_graph()` walks artifacts in order. Under target then artifact lock, `_sync_artifact()` acquires a missing exact
   blob, extracts and validates RECORD, hashes installed files, atomically promotes the target, writes identity metadata,
   and discards the blob.
6. Atomically write consumer/canonical manifests and transactionally record install provenance.
7. Optional alias generation, local copy, bytecode, and runtime activation happen only after a complete graph/target set.

| Material phase | Current behavior / workload | Locks or dependency | Safe unit | Proposal / bound | Evidence |
| --- | --- | --- | --- | --- | --- |
| Parse/identity | Ordered, negligible CPU | Stable request identity | none | Keep ordered | Not a measured bottleneck |
| uv planning | One plan subprocess, internally concurrent; executable validation may add one | Complete plan required | uv internal | Reuse uv; no outer pool | Cold observed two subprocess calls total; warm/partial zero |
| Simple/index metadata | Blocking, serial per selected distribution | Cohort and index/security policy | immutable planned key | Thread pool, shared bound 4 | 2.22x at four; eight regressed and produced a 30.726 s tail |
| Wheel acquire/hash | Blocking network + streaming hash | Per-artifact lock, exact size/hash, one-writer atomic blob | exact artifact | Thread pool, shared bound 4 | 2.57x medium cold, 1.95x partial; 1.70x 32-stratum replay; three largest regressed 11% |
| Wheel inspect | ZIP/metadata read after exact blob | Must precede dependency walk | acquired wheel | Finish in same worker, result immutable | Included in acquire timings |
| Source acquire/build | Network + build subprocess, potentially RAM heavy | Provenance, isolation, artifact lock | source project | Keep 1 initially; evaluate opt-in 2 | Three exact pure builds gained 1.27x at two and 1.72x at four; no native/compiler source exists in the plan |
| Extract/RECORD/promote | CPU/disk/hash, serial | Target then artifact lock; atomic complete target | exact artifact | Keep 1 | Four/eight regress 32–54% |
| Installed-target verify | CPU/filesystem/hash | Exact complete namespace | exact target | Keep 1 | Every tested bound above 1 regresses |
| Artifact metadata | Small atomic write | Artifact identity/lock | exact artifact | Keep ordered | Included in end-to-end only |
| Graph/manifests/install record | Deterministic shared state | Whole graph and canonical locks | none useful | Keep one writer | Integrity-sensitive, not remote wait |
| Local copy/bytecode | Optional disk/CPU | Complete immutable targets/destination | artifact | Keep 1 pending measurement | Outside default benchmark path |
| Activation/usage | Runtime/shared-owner state | Complete graph, leases, native owner | none | Keep ordered | Outside preparation critical path |

## Proposed implementation boundary

After `_bulk_plan()` succeeds, form immutable work keys containing cohort, distribution, selected version, effective index
set, transport/security policy, and cache identity. Do not run current `_resolve_candidate()` concurrently: `Resolver`
mutates `_artifacts`, `_nodes`, `_candidate_cache`, `_active_plan`, constraints, and index state.

Add a narrow helper beside `_project_artifact_payload()` that accepts explicit immutable policy and returns an immutable
candidate plus wheel inspection. Use one four-worker `ThreadPoolExecutor` per grouped operation to:

1. query/select metadata for unique planned keys;
2. fetch and exact-size/SHA-256 verify selected wheels under existing per-artifact locks;
3. inspect exact wheels; and
4. publish results on the caller thread in sorted key order before the existing recursive graph walk mutates state.

Treat artifacts at or above 100 MB as weight four so only one occupies the shared budget at a time. The threshold follows
the retained population boundary (nine exact wheels exceed 100 MB) and the measured 11% regression when the three largest
ran together. This is a small weighted-semaphore rule, not a second executor or adaptive scheduler.

For exact-manifest partial repair, split `_sync_artifact()` into an acquire-only step and the existing ordered
materialize/promote/record/discard step. Prefetch missing blobs with the same pool, then take target followed by artifact
locks in the existing order for extraction. Unplanned roots, VCS/local sources, source fallbacks, failed bulk cohorts, and
offline runs retain the serial path initially.

Expose one `max_io_workers` setting, default 16, minimum 1, maximum 32, capped by pending unique artifacts. The nominal
budget maps sub-1 MB artifacts to one slot, 1–10 MB artifacts to two, 10–100 MB artifacts and unknown-size metadata to
four, and 100+ MB artifacts to the full configured budget. Missing-size artifacts also consume the full budget until a
verified download records their observed size; only metadata uses an explicit four-slot unknown-size weight. The one/two/four
artifact weights remain fixed when capacity is raised, preserving the configured value as tiny-file capacity. This keeps
the measured four-way metadata/ordinary-artifact behavior while allowing sixteen tiny independent operations. It
must not control builds or local phases. Offline creates no network jobs. Preserve host/index isolation,
redirect/size/hash policy, retries and resume, credential redaction, per-artifact deduplication, and one-writer `os.replace`.

Workers return structured events/results. The caller renders progress by stable graph/cohort position, not completion
order. On failure, stop submitting, cancel unstarted futures, let running workers execute existing `finally` cleanup, and
raise the lowest stable-position typed error with other failures summarized in stable order. Never publish graph state or
an artifact result before hash and inspection succeed.

## Expected result and uncertainty

The only measured speedups are component speedups above. A four-worker implementation can remove roughly 2.92 seconds
from the measured 4.777-second cold acquire/inspect component and roughly 1.38 seconds from the 2.825-second partial
component if integration overhead and metadata dependencies do not erase the overlap. Against measured end-to-end medians
of 11.188 seconds cold and 2.441 seconds partial, no end-to-end number is claimed because the prototype does not yet
integrate resolver metadata prefetch and the component partial fixture is not identical to the end-to-end transfer path.

Warm should remain 0.330–0.341 seconds; this design intentionally offers no warm gain. The three exact Agent Zero pure
source builds improved from a 3.394-second median serially to 2.676 at two and 1.977 at four, but they do not establish
safe compiler-build concurrency and source builds stay serial initially. The 32-stratum wheel replay improved 1.70x at
four; eight delivered only another 0.185 seconds with 43% more CPU. The three largest wheels regressed from 97.529 seconds
serially to 108.298 at four, showing that one shared four-worker cap is a ceiling, not a promise to overlap every large
transfer. Full Agent Zero gain remains unknown because the 3.39 GB set includes nine source builds and nine 100+ MB
wheels. Public CDN
timing varies, overlay `ru_inblock` stayed zero and cannot distinguish page cache from physical reads, pylock omits graph
topology, and uv-internal request/wait detail is opaque. Peak RSS is a fresh-process high-water mark, not an instantaneous
sample. These are explicit rollout gates.

## Staged verification and rollback

1. Land the retained fixture/probe plus deterministic delayed-index tests; add request, active-connection, cancellation,
   and lock-wait counters around the implementation boundary.
2. Implement immutable planned-wheel prefetch behind an internal flag; caller-thread publication must keep graph IDs,
   manifests, aliases, progress order, and typed primary errors byte/stably identical at bounds 1, 2, 4, and 8.
3. Split exact-manifest acquire from ordered materialization without changing target/artifact lock order or blob cleanup.
4. Test wrong size/hash, corrupt ZIP/RECORD, truncation/resume, redirect, timeout, 429/5xx, retry exhaustion, cancellation,
   interruption leftovers, duplicate/overlapping graphs, uninstall/cleanup races, and Windows transient permissions.
5. Assert offline makes zero requests and existing allowed-host, insecure-transport, size, hash, RECORD, archive namespace,
   redaction, custom-index isolation, cache lifecycle, and removal tests pass unchanged.
6. Alternate connected one/four-worker runs. Enable default 4 only if median acquisition improves at least 2x, end-to-end
   medium cold improves at least 20%, warm median regresses no more than 15%, outputs are identical, and no failure/race
   invariant changes on Linux, macOS, or Windows.
7. Roll back immediately with `max_io_workers=1` on integrity/determinism differences, leaked temporary files, unstable
   error selection, excess rate limiting, or threshold regression. Keep source builds and local phases serial.

The measured proposal is therefore one bounded remote prefetch boundary followed by today’s deterministic ordered graph
and store completion—not blanket concurrency.

## Weighted implementation evidence

The unreleased implementation was rerun on the retained exact traces with the product scheduler at capacities 1, 4, 8,
and 16. The 33 correctness-asserting observations are retained in
[`io-concurrency-implementation-evidence.json`](io-concurrency-implementation-evidence.json). Every run verified every
distinct artifact size, SHA-256, and wheel identity; all observed opens completed without redirects or retries.

| Exact acquisition workload | Capacity 1 median | Capacity 4 median | Capacity 8 median | Weighted 16 median |
| --- | ---: | ---: | ---: | ---: |
| 2 tiny wheels / 79.6 KB | 0.232 s | 0.141 s | 0.140 s | 0.142 s |
| 26 mixed wheels / 18.6 MB | 4.654 s | 1.795 s | 1.556 s | 1.631 s |
| 13 partial-repair wheels / 16.7 MB | 2.775 s | 1.496 s | 1.462 s | 1.623 s |
| Agent Zero 32-stratum sample / 61.2 MB | 8.904 s | 5.100 s | 5.053 s | 5.131 s |

The exact three-largest Agent Zero cohort (1.415 GB total) ran once at weighted capacity 16 in 101.020 seconds while
verifying all three artifacts. Its 100+ MB weights admitted one transfer at a time, consistent with the accepted serial
97.529-second baseline and avoiding the accepted 108.298-second uniform-four regression. Public-network variance and a
single large rerun prevent claiming a speedup or regression from the 3.491-second difference. Scheduler tests separately
hold operations open and observe peak activity of 16, 8, 4, and 1 at the sub-1 MB, 1 MB, 10 MB, and 100 MB boundaries.

The deterministic boundary and mixed-order observations are retained in
[`io-scheduler-boundary-evidence.json`](io-scheduler-boundary-evidence.json). Equal-duration operations with one `.part`
file each measured peaks of 16 immediately below 1 MB, 8 at 1 MB and immediately below 10 MB, 4 at 10 MB and immediately
below 100 MB, and 1 at 100 MB and for missing sizes. Large-first, tiny-first, and missing-size-first cohorts kept large or
unknown work exclusive and later reached 16 tiny operations, with no remaining temporary files. In the deliberate
tiny/large/tiny order, the stable head-of-line rule admitted the first eight tiny operations, then held later tiny work
until the large operation could run alone; peak activity was therefore eight. This local admission probe measures queue,
active-operation, and cleanup behavior without claiming network throughput. The exact connected large-cohort result above
remains the transfer-performance evidence.
