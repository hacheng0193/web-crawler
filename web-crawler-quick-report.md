# Web Crawler Quick Report

> This report summarizes a 500-seed experiment with 8 worker threads and a configured runtime of 48 hours. It does not describe the crawler's default 10-minute configuration.

## Service Architecture

- The experiment starts from **500 seed URLs** and runs with **8 worker threads**.
- Each worker follows this processing path:

  `Frontier Queue → robots.txt check → Host delay check → Fetch HTML → Parse URLs → Normalize and filter URLs → Add new URLs to frontier → Persist results`

- URL discovery is deduplicated for the lifetime of one crawler run. URLs are normalized before insertion, and URLs that have already appeared are filtered out by `all_discovered`.
- Each fetched page contributes at most the first **500 extracted links** to the discovery step.

### Current frontier and rate-limit behavior

- The frontier maintains a separate priority queue for each **host (`netloc`)**.
- Priority is preserved within each host queue, while the outer host order uses ordinary **round-robin** scheduling to prevent one host from monopolizing the frontier.
- **Weighted round-robin is not implemented in the current code.** It should be treated as a possible future extension, not as a current behavior.
- `per_host_delay = 5 sec` spaces the start of HTML fetches to approximately **0.2 request/sec per host**. This is a host-level limit, not a registrable-domain-level limit.
- `robots.txt` requests are handled separately and occur before the HTML fetch delay check.

The current implementation has two important limitations:

1. A host can be placed back into the frontier order as soon as one URL is dequeued if that host still has pending URLs. There is no explicit host-level `in_flight` reservation that delays re-queueing until the request finishes.
2. The worker performs the host-delay wait itself. Therefore, the current implementation does not guarantee that workers remain fully available while one host is waiting for its next permitted request.

The architecture diagram is available here: [crawler-architecture](images/crawler-architecture.html)

## Results

| Metric | Max Depth = 3 | Max Depth = 10 |
|---|---:|---:|
| Configured Runtime | 48 hr | 48 hr |
| Discovered URLs | 2.66M | **4.98M** |
| Crawl Attempts | **397K** | 147K |
| Successful Crawls | **312K** | 117K |
| Failed Crawls | 84.6K | 30.0K |
| Success Rate | 78.7% | 79.6% |
| Pending Discovered URLs | 2.26M | **4.83M** |
| Host Origins with Robots State Recorded | 78.8K | **104.9K** |

The `Crawl Attempts` value includes both successful and failed fetches. `Pending Discovered URLs` corresponds to the URLs still represented in the frontier at the end of the run.

Sources: [depth 3 results](./crawl_summary.json), [depth 10 results](./crawl_summary_d=10.json)

## Key Observation

Increasing `max_depth` from **3 to 10** substantially increases discovery volume:

2.66M -> 4.98M


At the same time, the number of crawl attempts decreases:

397K -> 147K

Each crawl attempt produced approximately:

- **6.7 discovered URLs** at max depth 3
- **33.8 discovered URLs** at max depth 10

This indicates that deeper crawling produces substantially more discovery work per fetched page. The larger depth run also records more host origins with robots state:


78.8K -> 104.9K


### Current hypothesis

The lower number of crawl attempts at max depth 10 may be caused by a combination of:

- more URL parsing and normalization;
- more frontier insertions and deduplication checks;
- more result-writing activity;
- more host origins requiring robots.txt processing; and
- more requests being subject to the per-host delay.

The current summary files do not isolate the time spent in each stage, so this should remain a hypothesis rather than a confirmed root cause.

In short:

**Higher `max_depth` produces more discovered URLs per crawl attempt, but also increases discovery, host-management, and persistence work; the current data shows fewer crawl attempts but does not identify one proven cause.**

## Interpretation Notes

- The two runs used the same 500-seed experiment shape and 8-worker configuration, but the summary does not record every command-line option. The report therefore treats `per_host_delay = 5 sec` as the configured experiment setting and labels the runtime as configured runtime.
- `robots_hosts_cached` is reported as host-origin state recorded by the crawler. It does not necessarily mean that every robots.txt request succeeded, because unavailable robots endpoints can also leave a cached `None` state.
- The current report describes ordinary round-robin scheduling. A design in which a central scheduler reserves one host at a time, waits outside the workers, and only returns a host to the queue after its request completes would be a future scheduling improvement, not a behavior claimed by these results.
