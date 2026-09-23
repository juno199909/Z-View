import { createRequire } from 'node:module'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(import.meta.url)
const { chromium } = require(path.join(projectRoot, 'frontend', 'node_modules', 'playwright'))

const args = new Map()
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index]
  const value = process.argv[index + 1]
  if (key?.startsWith('--') && value) args.set(key.slice(2), value)
}

const transport = args.get('transport')
const durationSeconds = Math.max(10, Number(args.get('duration') || 60))
const wsUrl = args.get('ws-url')
const wtUrl = args.get('wt-url')
const certHash = args.get('cert-hash')
const pageUrl = args.get('page-url')
<<<<<<< HEAD
const captureBackend = args.get('capture-backend') || 'auto'
=======
>>>>>>> ab632a1a204ed06fef467d45a60e80c7a951259a

if (!['ws', 'wt'].includes(transport)) {
  throw new Error('usage: --transport ws|wt --duration 60 --ws-url URL or --wt-url URL --cert-hash HEX --page-url HTTPS_URL')
}
if (transport === 'ws' && !wsUrl) throw new Error('--ws-url is required for WebSocket mode')
if (transport === 'wt' && (!wtUrl || !certHash)) throw new Error('--wt-url and --cert-hash are required for WebTransport mode')
if (transport === 'wt' && !pageUrl) throw new Error('--page-url is required for WebTransport secure-context mode')
<<<<<<< HEAD
if (!['auto', 'dxgi', 'wgc', 'mss'].includes(captureBackend)) {
  throw new Error('--capture-backend must be auto, dxgi, wgc, or mss')
}
=======
>>>>>>> ab632a1a204ed06fef467d45a60e80c7a951259a

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ ignoreHTTPSErrors: true })
const page = await context.newPage()

try {
  if (pageUrl) {
    await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 15_000 })
  }
  const result = await page.evaluate(async (options) => {
    const textEncoder = new TextEncoder()
    const textDecoder = new TextDecoder()
    const startAt = performance.now()
    const rtts = []
    const state = {
      frames: 0,
      incompleteFrames: 0,
      datagrams: 0,
      pings: 0,
      lastFrameId: 0,
      firstMediaFrameMs: null,
      gatewayMedia: null,
      enginePipeline: null,
    }
    const timeoutAt = startAt + options.durationMs
    const makeControl = (payload) => JSON.stringify(payload)
    const addRtt = (timestamp) => {
      if (typeof timestamp === 'number') rtts.push(Math.max(0, performance.now() - timestamp))
    }
    const percentile = (values, p) => {
      if (!values.length) return null
      const sorted = [...values].sort((left, right) => left - right)
      return Number(sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p))].toFixed(1))
    }
    const finish = () => {
      const elapsedSeconds = Math.max(0.001, (performance.now() - startAt) / 1000)
      return {
        transport: options.transport,
        elapsed_seconds: Number(elapsedSeconds.toFixed(2)),
        target_fps: 60,
        received_frames: state.frames,
        received_fps: Number((state.frames / elapsedSeconds).toFixed(2)),
        incomplete_frames: state.incompleteFrames,
        received_datagrams: state.datagrams,
        first_media_frame_ms: state.firstMediaFrameMs,
        gateway_media: state.gatewayMedia,
        engine_pipeline: state.enginePipeline,
        control_rtt_ms: {
          samples: rtts.length,
          p50: percentile(rtts, 0.5),
          p95: percentile(rtts, 0.95),
          max: rtts.length ? Number(Math.max(...rtts).toFixed(1)) : null
        }
      }
    }
    const inspectBinaryFrame = (data, sendControl) => {
      const bytes = data instanceof Uint8Array ? data : new Uint8Array(data)
      if (bytes.byteLength < 17) return
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
      let offset = 0
      let newestH264FrameId = 0
      let hasFrame = false
      while (offset + 17 <= bytes.byteLength) {
        const type = view.getUint8(offset)
        const frameId = view.getUint32(offset + 1)
        const payloadLength = view.getUint32(offset + 13)
        if ((type !== 0x02 && type !== 0x03) || offset + 17 + payloadLength > bytes.byteLength) break
        hasFrame = true
        if (type === 0x03) newestH264FrameId = Math.max(newestH264FrameId, frameId)
        offset += 17 + payloadLength
      }
      if (!hasFrame) return
      state.frames += 1
      if (state.firstMediaFrameMs === null) {
        state.firstMediaFrameMs = Number((performance.now() - startAt).toFixed(1))
      }
      if (newestH264FrameId && newestH264FrameId !== state.lastFrameId) {
        state.lastFrameId = newestH264FrameId
        sendControl(makeControl({ type: 'frame_ack', seq: newestH264FrameId }))
      }
    }
    const handleText = (text) => {
      try {
        const message = JSON.parse(text)
        if (message.type === 'pong') addRtt(message.timestamp)
        if (message.type === 'transport_stats') {
          state.gatewayMedia = {
            frames_sent: Number(message.media_frames_sent || 0),
            datagrams_sent: Number(message.media_datagrams_sent || 0),
            frames_dropped: Number(message.media_frames_dropped || 0),
            keyframes_reliable: Number(message.media_keyframes_reliable || 0),
          }
        }
        if (message.type === 'pipeline_stats') {
          state.enginePipeline = {
            requested_fps: Number(message.fps_req || 0),
            sent_fps: Number(message.sent_fps || 0),
            capture_ms: Number(message.capture_ms || 0),
            encode_ms: Number(message.encode_ms || 0),
<<<<<<< HEAD
            encode_input_ms: Number(message.encode_input_ms || 0),
            encode_convert_ms: Number(message.encode_convert_ms || 0),
            encode_codec_ms: Number(message.encode_codec_ms || 0),
            encoder_backend: message.encoder_backend || null,
            encoder_hardware: Boolean(message.encoder_hardware),
            bitrate_bps: Number(message.bitrate_bps || 0),
            native_bridge: message.native_bridge || null,
=======
>>>>>>> ab632a1a204ed06fef467d45a60e80c7a951259a
            queue_depth: Number(message.queue_depth || 0),
            skipped: Number(message.skipped || 0),
            empty: Number(message.empty || 0),
            backpressure_drops: Number(message.drops_bp || 0),
            codec: message.codec || null,
            backend: message.backend || null,
          }
        }
      } catch {
        // Non-JSON diagnostic text is irrelevant to the benchmark.
      }
    }

    if (options.transport === 'ws') {
      return await new Promise((resolve, reject) => {
        const socket = new WebSocket(options.wsUrl)
        socket.binaryType = 'arraybuffer'
        let timer = null
        const stop = () => {
          clearInterval(timer)
          try { socket.close() } catch {}
          resolve(finish())
        }
        socket.onerror = () => reject(new Error('WebSocket benchmark connection failed'))
        socket.onopen = () => {
          socket.send(makeControl({ type: 'viewer_capabilities', webcodecs: true }))
          socket.send(makeControl({
            type: 'settings',
            quality: 95,
            fps: 60,
            scale_percent: 100,
            adaptive: false,
            preset: 'high',
<<<<<<< HEAD
            capture_backend: options.captureBackend,
=======
>>>>>>> ab632a1a204ed06fef467d45a60e80c7a951259a
          }))
          timer = setInterval(() => {
            if (performance.now() >= timeoutAt) return stop()
            const timestamp = performance.now()
            state.pings += 1
            socket.send(makeControl({ type: 'ping', timestamp }))
          }, 1000)
        }
        socket.onmessage = (event) => {
          if (typeof event.data === 'string') handleText(event.data)
          else inspectBinaryFrame(event.data, (payload) => socket.send(payload))
        }
      })
    }

    const hexToBytes = (hex) => {
      const bytes = new Uint8Array(hex.length / 2)
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16)
      }
      return bytes
    }
    const wt = new WebTransport(options.wtUrl, {
      serverCertificateHashes: [{ algorithm: 'sha-256', value: hexToBytes(options.certHash) }]
    })
    await wt.ready
    const stream = await wt.createBidirectionalStream()
    const writer = stream.writable.getWriter()
    const streamReader = stream.readable.getReader()
    const datagramReader = wt.datagrams.readable.getReader()
    const sendReliable = (text) => {
      const payload = textEncoder.encode(text)
      const frame = new Uint8Array(5 + payload.length)
      new DataView(frame.buffer).setUint32(0, payload.length)
      frame[4] = 0
      frame.set(payload, 5)
      writer.write(frame).catch(() => {})
    }
    sendReliable(makeControl({ type: 'transport_ready', transport: 'benchmark-wt' }))
    sendReliable(makeControl({ type: 'viewer_capabilities', webcodecs: true }))
    sendReliable(makeControl({
      type: 'settings',
      quality: 95,
      fps: 60,
      scale_percent: 100,
      adaptive: false,
      preset: 'high',
<<<<<<< HEAD
      capture_backend: options.captureBackend,
=======
>>>>>>> ab632a1a204ed06fef467d45a60e80c7a951259a
    }))

    const frames = new Map()
    const expireFrames = () => {
      const now = performance.now()
      for (const [frameId, frame] of frames) {
        if (now - frame.updatedAt > 250) {
          frames.delete(frameId)
          state.incompleteFrames += 1
        }
      }
    }
    const consumeDatagram = (value) => {
      const datagram = value instanceof Uint8Array ? value : new Uint8Array(value)
      if (datagram.byteLength < 12 || datagram[0] !== 0x5a || datagram[1] !== 0x56 || datagram[2] !== 0x4d || datagram[3] !== 0x44) return
      const view = new DataView(datagram.buffer, datagram.byteOffset, datagram.byteLength)
      const frameId = view.getUint32(4)
      const partIndex = view.getUint16(8)
      const partCount = view.getUint16(10)
      if (!partCount || partCount > 512 || partIndex >= partCount) return
      expireFrames()
      let frame = frames.get(frameId)
      if (!frame) {
        frame = { parts: new Array(partCount), count: 0, updatedAt: performance.now() }
        frames.set(frameId, frame)
      }
      if (frame.parts.length !== partCount || frame.parts[partIndex]) return
      frame.parts[partIndex] = datagram.slice(12)
      frame.count += 1
      frame.updatedAt = performance.now()
      state.datagrams += 1
      if (frame.count === partCount) {
        frames.delete(frameId)
        const size = frame.parts.reduce((sum, part) => sum + part.byteLength, 0)
        const media = new Uint8Array(size)
        let offset = 0
        for (const part of frame.parts) {
          media.set(part, offset)
          offset += part.byteLength
        }
        inspectBinaryFrame(media, sendReliable)
      }
    }
    const readWireStream = async (sourceReader) => {
      let buffer = new Uint8Array(0)
      while (true) {
        const { value, done } = await sourceReader.read()
        if (done) return
        const chunk = value instanceof Uint8Array ? value : new Uint8Array(value)
        const joined = new Uint8Array(buffer.length + chunk.length)
        joined.set(buffer)
        joined.set(chunk, buffer.length)
        buffer = joined
        while (buffer.length >= 5) {
          const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength)
          const length = view.getUint32(0)
          if (buffer.length < 5 + length) break
          const type = buffer[4]
          const payload = buffer.slice(5, 5 + length)
          buffer = buffer.slice(5 + length)
          if (type === 0) handleText(textDecoder.decode(payload))
          else inspectBinaryFrame(payload, sendReliable)
        }
      }
    }
    const readReliable = readWireStream(streamReader)
    const readKeyframeStreams = (async () => {
      const incomingReader = wt.incomingUnidirectionalStreams?.getReader()
      if (!incomingReader) return
      while (true) {
        const { value: incomingStream, done } = await incomingReader.read()
        if (done) return
        readWireStream(incomingStream.getReader()).catch(() => {})
      }
    })()
    const readDatagrams = (async () => {
      while (true) {
        const { value, done } = await datagramReader.read()
        if (done) return
        consumeDatagram(value)
      }
    })()

    await new Promise((resolve) => {
      const timer = setInterval(() => {
        expireFrames()
        if (performance.now() >= timeoutAt) {
          clearInterval(timer)
          resolve()
          return
        }
        const timestamp = performance.now()
        state.pings += 1
        sendReliable(makeControl({ type: 'ping', timestamp }))
      }, 1000)
    })
    for (const frameId of frames.keys()) {
      frames.delete(frameId)
      state.incompleteFrames += 1
    }
    try { await writer.close() } catch {}
    try { wt.close() } catch {}
    await Promise.race([
      Promise.allSettled([readReliable, readDatagrams, readKeyframeStreams]),
      new Promise((resolve) => setTimeout(resolve, 1000))
    ])
    return finish()
  }, {
    transport,
    durationMs: durationSeconds * 1000,
    wsUrl,
    wtUrl,
<<<<<<< HEAD
    certHash,
    captureBackend,
=======
    certHash
>>>>>>> ab632a1a204ed06fef467d45a60e80c7a951259a
  })
  console.log(JSON.stringify(result, null, 2))
} finally {
  await browser.close()
}
