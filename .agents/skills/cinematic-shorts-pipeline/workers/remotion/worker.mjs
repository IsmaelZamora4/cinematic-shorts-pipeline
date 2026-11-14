import {createHash} from "node:crypto";
import {mkdir, readFile, stat, writeFile} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {bundle} from "@remotion/bundler";
import {renderMedia, selectComposition} from "@remotion/renderer";

const MAX_REQUEST_BYTES = 128 * 1024;
const MAX_WAV_BYTES = 16 * 1024 * 1024;
const MAX_MP4_BYTES = 64 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 64 * 1024;
const FRAME_COUNT = 24;
const SAMPLES_PER_FRAME = 2000;
const TOTAL_SAMPLES = FRAME_COUNT * SAMPLES_PER_FRAME;
const REQUEST_FIELDS = [
  "record_type", "request_version", "project_id", "run_id", "width", "height", "fps",
  "frames", "source_samples", "silence_samples", "inputs", "captions", "anchors",
];

const fail = (message) => { throw new Error(message); };

const exactKeys = (object, expected) => object && typeof object === "object" && !Array.isArray(object)
  && Object.keys(object).sort().join("\0") === [...expected].sort().join("\0");

const parseRequest = (request) => {
  if (!exactKeys(request, REQUEST_FIELDS)) fail("request fields do not match the fixed contract");
  if (request.record_type !== "remotion-worker-request" || request.request_version !== 1
    || typeof request.project_id !== "string" || !request.project_id.trim()
    || typeof request.run_id !== "string" || !request.run_id.trim()
    || request.width !== 540 || request.height !== 960 || request.fps !== 12 || request.frames !== FRAME_COUNT
    || !Number.isInteger(request.source_samples) || request.source_samples < 1 || request.source_samples > TOTAL_SAMPLES
    || request.silence_samples !== TOTAL_SAMPLES - request.source_samples) {
    fail("request does not match the fixed composition contract");
  }
  const expectedInputs = {
    "validated-plan": "validated-plan.json",
    storyboard: "storyboard.json",
    "scene-package": "scene-package.zip",
    narration: "narration.wav",
    "narration-provenance": "narration-provenance.json",
  };
  if (!exactKeys(request.inputs, Object.keys(expectedInputs))
    || Object.entries(expectedInputs).some(([key, value]) => request.inputs[key] !== value)) {
    fail("request input set is not fixed");
  }
  if (!Array.isArray(request.captions) || !Array.isArray(request.anchors)) fail("captions and anchors must be arrays");
  request.captions.forEach((caption, index) => {
    if (!exactKeys(caption, ["caption_index", "start_frame", "end_frame_exclusive", "start_ms", "end_ms", "text"])
      || caption.caption_index !== index || !Number.isInteger(caption.start_frame)
      || !Number.isInteger(caption.end_frame_exclusive) || caption.start_frame < 0
      || caption.end_frame_exclusive <= caption.start_frame || caption.end_frame_exclusive > FRAME_COUNT
      || !Number.isInteger(caption.start_ms) || !Number.isInteger(caption.end_ms)
      || caption.start_ms < 0 || caption.end_ms <= caption.start_ms || caption.end_ms > 2000
      || typeof caption.text !== "string" || !caption.text.trim()) fail(`caption ${index} is malformed`);
  });
  request.anchors.forEach((anchor, index) => {
    if (!exactKeys(anchor, ["id", "scene_id", "kind", "start_ms", "end_ms", "basis"])
      || typeof anchor.id !== "string" || typeof anchor.scene_id !== "string"
      || !["beat", "pause", "cut"].includes(anchor.kind)
      || !["measured", "manually_reviewed"].includes(anchor.basis)
      || !Number.isInteger(anchor.start_ms) || !Number.isInteger(anchor.end_ms)
      || anchor.start_ms < 0 || anchor.end_ms > 2000 || anchor.end_ms < anchor.start_ms
      || (anchor.kind === "cut" ? anchor.end_ms !== anchor.start_ms : anchor.end_ms === anchor.start_ms)) {
      fail(`anchor ${index} is malformed`);
    }
  });
  return request;
};

const verifyWav = (wav, expectedSamples) => {
  if (wav.length < 44 || wav.length > MAX_WAV_BYTES || wav.toString("ascii", 0, 4) !== "RIFF"
    || wav.toString("ascii", 8, 12) !== "WAVE" || wav.readUInt32LE(4) !== wav.length - 8) {
    fail("narration WAV is malformed or out of bounds");
  }
  let offset = 12;
  let format;
  let pcm;
  while (offset + 8 <= wav.length) {
    const id = wav.toString("ascii", offset, offset + 4);
    const size = wav.readUInt32LE(offset + 4);
    const start = offset + 8;
    const end = start + size;
    if (end > wav.length) fail("narration WAV has a truncated chunk");
    if (id === "fmt ") {
      if (format) fail("narration WAV has duplicate format chunks");
      format = wav.subarray(start, end);
    } else if (id === "data") {
      if (pcm !== undefined || size % 2) fail("narration WAV has invalid sample data");
      pcm = wav.subarray(start, end);
    }
    offset = end + (size & 1);
  }
  if (offset !== wav.length || !format || format.length < 16 || !pcm || pcm.length / 2 !== expectedSamples
    || format.readUInt16LE(0) !== 1 || format.readUInt16LE(2) !== 1
    || format.readUInt32LE(4) !== 24000 || format.readUInt16LE(12) !== 2
    || format.readUInt16LE(14) !== 16) fail("narration WAV does not match the verified mono PCM16/24 kHz request");
  return pcm;
};

const makePaddedWav = (sourcePcm, silenceSamples) => {
  const pcm = Buffer.concat([sourcePcm, Buffer.alloc(silenceSamples * 2)]);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write("WAVE", 8, "ascii");
  header.write("fmt ", 12, "ascii");
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(24000, 24);
  header.writeUInt32LE(48000, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36, "ascii");
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
};

const main = async () => {
  if (process.argv.length !== 3) fail("expected exactly one worker request path");
  const requestPath = path.resolve(process.argv[2]);
  const requestStat = await stat(requestPath);
  if (!requestStat.isFile() || requestStat.size > MAX_REQUEST_BYTES) fail("request file is not regular or exceeds its limit");
  const requestBytes = await readFile(requestPath);
  if (requestBytes.length > MAX_REQUEST_BYTES) fail("request file exceeds its limit");
  const request = parseRequest(JSON.parse(requestBytes.toString("utf8")));
  const staging = path.dirname(requestPath);
  const publicDir = path.join(staging, "public");
  const wavPath = path.join(publicDir, "narration-padded.wav");
  await mkdir(publicDir, {mode: 0o700});
  const narrationPath = path.join(staging, request.inputs.narration);
  const wavStat = await stat(narrationPath);
  if (!wavStat.isFile() || wavStat.size > MAX_WAV_BYTES) fail("narration WAV is not regular or exceeds its limit");
  const sourceWav = await readFile(narrationPath);
  if (!wavStat.isFile() || wavStat.size !== sourceWav.length) fail("narration WAV changed while it was read");
  const pcm = verifyWav(sourceWav, request.source_samples);
  await writeFile(wavPath, makePaddedWav(pcm, request.silence_samples), {flag: "wx", mode: 0o600});

  const framesDir = path.join(publicDir, "frames");
  await mkdir(framesDir, {mode: 0o700});
  for (let frame = 0; frame < FRAME_COUNT; frame += 1) {
    const name = `frame-${String(frame).padStart(6, "0")}.png`;
    const source = path.join(staging, "frames", name);
    const target = path.join(framesDir, name);
    const sourceInfo = await stat(source);
    if (!sourceInfo.isFile() || sourceInfo.size < 33 || sourceInfo.size > 32 * 1024 * 1024) fail(`frame ${frame} is invalid`);
    const png = await readFile(source);
    if (png.length !== sourceInfo.size || png.toString("hex", 0, 8) !== "89504e470d0a1a0a") fail(`frame ${frame} changed or is not PNG`);
    await writeFile(target, png, {flag: "wx", mode: 0o600});
  }

  const workerDir = path.dirname(fileURLToPath(import.meta.url));
  const serveUrl = await bundle({entryPoint: path.join(workerDir, "entry.mjs"), publicDir});
  const inputProps = {captions: request.captions};
  const composition = await selectComposition({serveUrl, id: "FixedStage04", inputProps});
  const outputPath = path.join(staging, "composite.mp4");
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    pixelFormat: "yuv420p",
    audioCodec: "aac",
    audioBitrate: "192k",
    sampleRate: 48000,
    outputLocation: outputPath,
    inputProps,
    concurrency: 1,
    logLevel: "error",
  });
  const outputStat = await stat(outputPath);
  if (!outputStat.isFile() || outputStat.size < 1 || outputStat.size > MAX_MP4_BYTES) fail("Remotion output is invalid or exceeds 64 MiB");
  const output = await readFile(outputPath);
  const response = {
    record_type: "remotion-worker-result",
    response_version: 1,
    run_id: request.run_id,
    success: true,
    frames_rendered: FRAME_COUNT,
    output_path: "composite.mp4",
    output_size_bytes: output.length,
    output_sha256: createHash("sha256").update(output).digest("hex"),
  };
  const responseBytes = Buffer.from(`${JSON.stringify(response)}\n`, "utf8");
  if (responseBytes.length > MAX_RESPONSE_BYTES) fail("worker response exceeds its limit");
  await writeFile(path.join(staging, "response.json"), responseBytes, {flag: "wx", mode: 0o600});
};

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : "worker failed"}\n`);
  process.exitCode = 1;
});
