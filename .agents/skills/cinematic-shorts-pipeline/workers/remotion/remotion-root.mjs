import React from "react";
import {AbsoluteFill, Audio, Composition, Img, Sequence, staticFile, useCurrentFrame} from "remotion";

const Frame = ({frame}) => React.createElement(Img, {
  src: staticFile(`frames/frame-${String(frame).padStart(6, "0")}.png`),
  style: {width: 540, height: 960, objectFit: "fill"},
});

const CaptionTrack = ({captions}) => {
  const frame = useCurrentFrame();
  const active = captions.filter((caption) => frame >= caption.start_frame && frame < caption.end_frame_exclusive);
  if (active.length === 0) return null;
  return React.createElement(AbsoluteFill, {style: {justifyContent: "flex-end", alignItems: "center", padding: "0 36px 92px"}},
    ...active.map((caption) => React.createElement("div", {
      key: caption.caption_index,
      style: {
        maxWidth: "100%", marginTop: 8, color: "#ffffff", backgroundColor: "rgba(0,0,0,0.78)",
        padding: "14px 20px", borderRadius: 10, fontFamily: "Arial, sans-serif",
        fontSize: 34, fontWeight: 700, lineHeight: 1.15, textAlign: "center",
        whiteSpace: "pre-wrap", overflowWrap: "anywhere",
      },
    }, caption.text)),
  );
};

const FixedComposition = ({captions}) => React.createElement(AbsoluteFill, {style: {backgroundColor: "#000000"}},
  Array.from({length: 24}, (_, frame) => React.createElement(Sequence, {
    key: frame, from: frame, durationInFrames: 1,
  }, React.createElement(Frame, {frame}))),
  React.createElement(Audio, {src: staticFile("narration-padded.wav"), volume: 1}),
  React.createElement(CaptionTrack, {captions}),
);

export const RemotionRoot = () => React.createElement(Composition, {
  id: "FixedStage04", component: FixedComposition, durationInFrames: 24,
  fps: 12, width: 540, height: 960, defaultProps: {captions: []},
});
