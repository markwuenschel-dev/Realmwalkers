// The drafter emits values; the worker draws an aligned Unicode box into Scene.prose. That box only
// reads as aligned in a MONOSPACE context — the prose editor is serif, which made it look ragged.
// This renders prose for reading: narrative stays serif; each box-drawing block is shown in a
// monospace <pre> so its columns and borders line up exactly as the renderer computed them.

// Every rendered box line begins (at the left edge) with one of these box-drawing characters.
const BOX_EDGE = /^\s*[┌│├└]/; // ┌ │ ├ └

type Segment = { kind: "prose" | "box"; text: string };

function segment(text: string): Segment[] {
  const segs: Segment[] = [];
  let buf: string[] = [];
  let mode: Segment["kind"] = "prose";
  const flush = () => {
    if (buf.length) segs.push({ kind: mode, text: buf.join("\n") });
    buf = [];
  };
  for (const line of text.split("\n")) {
    const lineMode: Segment["kind"] = BOX_EDGE.test(line) ? "box" : "prose";
    if (lineMode !== mode) {
      flush();
      mode = lineMode;
    }
    buf.push(line);
  }
  flush();
  return segs;
}

export default function RenderedProse({ text }: { text: string }) {
  return (
    <div className="rendered">
      {segment(text).map((seg, i) =>
        seg.kind === "box" ? (
          <pre key={i} className="statbox">
            {seg.text}
          </pre>
        ) : (
          <div key={i} className="narr">
            {seg.text}
          </div>
        ),
      )}
    </div>
  );
}
