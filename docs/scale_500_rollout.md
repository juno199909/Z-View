# 300-500 Terminal Production Baseline

## Service boundaries

- Control plane: two API instances behind a reverse proxy. Keep HTTP APIs stateless;
  use shared session state and sticky routing only for WebSocket control connections.
- Media plane: WebTransport gateway runs separately from the API instances. Use an
  L4 UDP load balancer with session-consistent routing before adding a second gateway.
- Data plane: MySQL is a dedicated service with backups and a connection pool. Package
  binaries belong in object storage or a dedicated download service, not on API disks.
- Background work: compliance scans and bulk distribution must use a worker queue.
  API processes only create jobs and return task IDs.

## Remote desktop admission profile

| Agent media capability | Default maximum | Intended use |
| --- | --- | --- |
| NVENC/QSV/AMF or native GPU bridge | 1080p, 60 FPS | High-motion LAN work |
| libx264 software encoding | 75% scale, 30 FPS | Typical office endpoint |
| No confirmed H.264 encoder | 70% scale, 15 FPS | Compatibility fallback |

The Agent enforces these limits. The platform also caps the requested FPS when
it creates a session, based on the last reported capability. An older Agent
without a capability report receives the 30 FPS stable profile.

## Capacity gates

Do not promote a configuration based on terminal count alone. Run 60-second
TCP and QUIC sessions at 10, 30, and 50 concurrent viewers. Record p95 control
RTT, received FPS, incomplete frames, Agent encode time, platform CPU, UDP
gateway CPU, and outbound bandwidth. A tier is accepted only when p95 control
RTT stays below 100 ms on LAN, incomplete frames remain below 1%, and the
Agent sustains at least 80% of its negotiated FPS without encode backlog.

For a 500-terminal rollout, stage software distribution in waves of 25-50
endpoints. Increase a wave only after download and install failure rates remain
below 2%; do not start a fleet-wide download from the API server's local disk.
