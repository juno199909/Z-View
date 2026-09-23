# Remote Transport Benchmark

Run two newly-created sessions for the same asset and settings, first over
WebSocket/TCP and then over WebTransport/QUIC. The benchmark reports completed
video frames, achieved FPS, incomplete media frames, received datagrams,
reliable keyframes, Agent capture/encode telemetry, and control-plane ping RTT.

Obtain `ws_url`, `wt_url`, and `wt_cert_hash` from the remote-session create
response. Use a secure frontend URL for `--page-url`, because browser
WebTransport is only available in a secure context.

```powershell
node scripts/remote_transport_benchmark.mjs `
  --transport ws `
  --duration 60 `
  --ws-url "wss://platform.example/api/v1/remote/sessions/123/ws?token=..."

node scripts/remote_transport_benchmark.mjs `
  --transport wt `
  --duration 60 `
  --page-url "https://platform.example:4173" `
  --wt-url "https://platform.example:4433/webtransport?token=...&session_id=123" `
  --cert-hash "<wt_cert_hash>"
```

Use a 60 FPS high-quality session for both runs. Compare `received_fps`, the
`control_rtt_ms.p95` value, `incomplete_frames`, and `engine_pipeline`. A UDP
result is a rollout candidate only when its frame rate is comparable or higher,
p95 control RTT is lower, and incomplete frames remain near zero under the same
network condition. H.264 keyframes should appear in
`gateway_media.keyframes_reliable`; delta frames remain on datagrams.
