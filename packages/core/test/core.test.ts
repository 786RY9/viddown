import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  classifyError,
  filterVideoFormats,
  formatBytes,
  formatDuration,
  jobArgs,
  LineSplitter,
  parseLine,
  parseTime,
  phaseSegments,
  ProgressTracker,
  toSeconds,
  type JobSpec,
  type YtFormat,
} from "../src";

const fixture = <T>(name: string): T =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`../../../fixtures/${name}`, import.meta.url)), "utf8"));

describe("parity with cli/viddown_core (shared fixtures)", () => {
  const time = fixture<{ cases: { input: string; expected: string | null }[] }>("parse_time.json");
  it.each(time.cases)("parseTime($input)", ({ input, expected }) => {
    expect(parseTime(input)).toBe(expected);
  });

  const formats = fixture<{ cases: { name: string; formats: YtFormat[]; expected: string[] }[] }>(
    "filter_formats.json",
  );
  it.each(formats.cases)("filterVideoFormats: $name", ({ formats: input, expected }) => {
    expect(filterVideoFormats(input).map((f) => f.format_id)).toEqual(expected);
  });
});

describe("formatting", () => {
  it("formats", () => {
    expect(toSeconds("01:05:30")).toBe(3930);
    expect(formatDuration(83)).toBe("1:23");
    expect(formatDuration(3930)).toBe("1:05:30");
    expect(formatDuration(null)).toBe("--:--");
    expect(formatBytes(1_572_864)).toBe("1.5 MB");
    expect(formatBytes(512)).toBe("512 B");
  });
});

const spec = (over: Partial<JobSpec> = {}): JobSpec => ({
  id: "j1",
  url: "https://www.youtube.com/watch?v=x",
  kind: "video",
  formatId: "299",
  outputDir: "/home/u/Videos/",
  createdAt: 0,
  ...over,
});

describe("jobArgs", () => {
  it("builds a merged video job with machine-readable output, URL last after --", () => {
    const a = jobArgs(spec(), { concurrentFragments: 4, ffmpegLocation: "/opt/ffmpeg" });
    expect(a.slice(0, 2)).toEqual(["-f", "299+bestaudio/best"]);
    expect(a[a.indexOf("--concurrent-fragments") + 1]).toBe("4");
    expect(a[a.indexOf("-o") + 1]).toBe("/home/u/Videos/%(title).150B [%(height)sp].%(ext)s");
    expect(a).toContain("--no-simulate");
    expect(a[a.indexOf("--ffmpeg-location") + 1]).toBe("/opt/ffmpeg");
    expect(a.slice(-2)).toEqual(["--", "https://www.youtube.com/watch?v=x"]);
  });

  it("builds an audio clip job", () => {
    const a = jobArgs(spec({ kind: "audio", clip: { start: "00:00:05", end: "00:00:15" } }), { sep: "\\" });
    expect(a).toContain("-x");
    expect(a[a.indexOf("--download-sections") + 1]).toBe("*00:00:05-00:00:15");
    expect(a[a.indexOf("-o") + 1]).toContain("\\%(title).150B [audio] [%(section_start)s-%(section_end)s]");
  });
});

describe("progress", () => {
  it("splits CR/LF streams", () => {
    const s = new LineSplitter();
    expect(s.feed("a\r\nb\rc")).toEqual(["a", "b"]);
    expect(s.feed("d\n")).toEqual(["cd"]);
    expect(s.flush()).toEqual([]);
  });

  it("parses template lines", () => {
    expect(parseLine('[vd:dl] {"status":"downloading","downloaded_bytes":50,"total_bytes":100,"speed":10,"eta":5,"filename":"a.f1"}'))
      .toMatchObject({ type: "download", downloaded: 50, total: 100, filename: "a.f1" });
    expect(parseLine("[vd:pp] started Merger")).toEqual({ type: "postprocess", status: "started", name: "Merger" });
    expect(parseLine("[vd:file] /x/y.mp4")).toEqual({ type: "file", path: "/x/y.mp4" });
    expect(parseLine("ERROR: boom")).toEqual({ type: "error", message: "ERROR: boom" });
    expect(parseLine("   ")).toBeNull();
  });

  it("tracks two-stream jobs monotonically", () => {
    const t = new ProgressTracker("video");
    const dl = (filename: string, downloaded: number, total = 100) =>
      t.push({ type: "download", status: "downloading", filename, downloaded, total });

    expect(dl("v", 50)?.fraction).toBeCloseTo(0.408);
    expect(t.progress.phase).toBe("video");
    expect(dl("v", 40)?.fraction).toBeCloseTo(0.408); // never goes backwards
    expect(dl("a", 50)?.fraction).toBeCloseTo(0.888);
    expect(t.progress.phase).toBe("audio");
    expect(t.push({ type: "postprocess", status: "started", name: "Merger" })?.phase).toBe("merging");
    expect(t.push({ type: "postprocess", status: "started", name: "MoveFiles" })).toBeNull();
    expect(t.finish().fraction).toBe(1);
  });

  it("phase segments line up with tracker fractions", () => {
    const segs = phaseSegments("video");
    expect(segs.map((s) => s.label)).toEqual(["Video", "Audio", "Merge"]);
    expect(segs.reduce((a, s) => a + s.weight, 0)).toBeCloseTo(1);
    const t = new ProgressTracker("video");
    const end = t.push({ type: "download", status: "finished", filename: "v", downloaded: 1, total: 1 });
    expect(end?.fraction).toBeCloseTo(segs[0]!.weight);
  });

  it("uses fragment counts when sizes are unknown", () => {
    const t = new ProgressTracker("audio");
    expect(t.push({ type: "download", status: "downloading", filename: "a", fragIndex: 5, fragCount: 10 })?.fraction)
      .toBeCloseTo(0.48);
  });
});

describe("classifyError", () => {
  it.each([
    ["ERROR: [youtube] x: Sign in to confirm you're not a bot", "blocked"],
    ["ERROR: unable to download: HTTP Error 403: Forbidden", "blocked"],
    ["ERROR: [youtube] x: Private video. Sign in if you've been granted access", "unavailable"],
    ["ERROR: Unsupported URL: https://example.com", "unsupported"],
    ["noise\nERROR: Connection reset by peer", "network"],
    ["ERROR: something odd", "unknown"],
  ])("%s -> %s", (msg, kind) => {
    expect(classifyError(msg).kind).toBe(kind);
  });

  it("strips the ERROR prefix and keeps the last error line", () => {
    expect(classifyError("ERROR: first\nERROR: second").message).toBe("second");
  });
});

describe("fixes from desktop verification", () => {
  it("strips trailing backslashes and slashes from the output dir", () => {
    const a = jobArgs(spec({ outputDir: String.raw`C:\Users\me\Downloads\viddown\\` }), { sep: "\\" });
    expect(a[a.indexOf("-o") + 1]).toBe(String.raw`C:\Users\me\Downloads\viddown\%(title).150B [%(height)sp].%(ext)s`);
  });

  it("treats 404/410 as unavailable (not retried)", () => {
    expect(classifyError("ERROR: unable to download webpage: HTTP Error 404: Not Found").kind).toBe("unavailable");
  });

  it("labels single-stream audio jobs as audio", () => {
    const t = new ProgressTracker("audio");
    expect(t.push({ type: "download", status: "downloading", filename: "a", downloaded: 1, total: 2 })?.phase).toBe("audio");
  });

  it("parses ffmpeg time= lines and ignores time=N/A", () => {
    expect(parseLine("frame=  120 fps= 30 q=-1.0 size=  1024kB time=00:01:05.50 bitrate= 128.0kbits/s")).toEqual({
      type: "ffmpeg",
      seconds: 65.5,
    });
    expect(parseLine("size=N/A time=N/A bitrate=N/A")?.type).toBe("log");
  });

  it("derives clip progress from ffmpeg position, across both streams", () => {
    const t = ProgressTracker.forJob({ kind: "video", clip: { start: "00:00:10", end: "00:00:30" } });
    const ff = (s: number) => t.push({ type: "ffmpeg", seconds: s });
    expect(ff(10)?.fraction).toBeCloseTo(0.5 * 0.85 * 0.96);
    expect(t.progress.phase).toBe("video");
    expect(ff(20)?.fraction).toBeCloseTo(0.85 * 0.96);
    expect(ff(10)?.phase).toBe("audio"); // position restarted -> second stream
    expect(t.progress.fraction).toBeCloseTo((0.85 + 0.5 * 0.15) * 0.96);
  });

  it("ignores ffmpeg lines for whole-video jobs", () => {
    expect(ProgressTracker.forJob({ kind: "video", clip: null }).push({ type: "ffmpeg", seconds: 5 })).toBeNull();
  });
});
