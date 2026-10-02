"use client";

/**
 * SoundControl — the alert-sound button in the global bar and the small panel
 * it opens: mute, a volume slider, and a button to hear the result.
 *
 * It owns nothing but whether its panel is open. The volume and the mute
 * switch live in MapApp (which also plays the alerts) and are remembered per
 * browser there.
 *
 * Mute and volume are separate on purpose: muting keeps the level, so
 * unmuting brings back the volume the controller had chosen instead of some
 * default. Dragging the slider up from silence unmutes; dragging it to zero
 * is the same as muting.
 */

import { useEffect, useRef, useState } from "react";

import NavIcon from "@/components/nav/NavIcon";

interface Props {
  muted: boolean;
  /** 0–100. */
  volume: number;
  onToggleMute: () => void;
  onVolume: (volume: number) => void;
  /** Play a sample alert at the current setting. */
  onTest: () => void;
  /** Called when the panel opens, so the bar can close its own dropdown. */
  onOpen?: () => void;
}

export default function SoundControl({
  muted,
  volume,
  onToggleMute,
  onVolume,
  onTest,
  onOpen,
}: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // A click anywhere else, or Escape, closes the panel. On mousedown so it
  // beats the click that would otherwise re-open it.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const silent = muted || volume === 0;
  const label = silent ? "Alert sounds are off" : `Alert volume ${volume}%`;

  return (
    <div className="mnav-sound" ref={rootRef}>
      <button
        type="button"
        className={`mnav-util-btn${silent ? " active" : ""}`}
        onClick={() => {
          if (!open) onOpen?.();
          setOpen((o) => !o);
        }}
        title={`${label} — click to change`}
        aria-label={`${label}. Open the sound settings`}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <NavIcon name={silent ? "volume-off" : "volume"} size={15} />
      </button>

      {open && (
        <div className="mnav-sound-pop" role="dialog" aria-label="Alert sound">
          <div className="mnav-sound-head">
            <span>Alert sound</span>
            <output aria-live="polite">{silent ? "Off" : `${volume}%`}</output>
          </div>
          <div className="mnav-sound-row">
            <button
              type="button"
              className={`mnav-util-btn${muted ? " active" : ""}`}
              onClick={onToggleMute}
              title={muted ? "Unmute alert sounds" : "Mute alert sounds"}
              aria-label={muted ? "Unmute alert sounds" : "Mute alert sounds"}
              aria-pressed={muted}
            >
              <NavIcon name={silent ? "volume-off" : "volume"} size={15} />
            </button>
            <input
              type="range"
              className="mnav-sound-slider"
              min={0}
              max={100}
              step={5}
              value={volume}
              onChange={(e) => onVolume(Number(e.target.value))}
              // Hear the new level once the hand comes off the control: on
              // every step it would be a machine-gun of beeps.
              onPointerUp={onTest}
              onKeyUp={(e) => {
                if (e.key.startsWith("Arrow") || e.key === "Home" || e.key === "End") onTest();
              }}
              aria-label="Alert volume"
              aria-valuetext={silent ? "Off" : `${volume}%`}
            />
          </div>
          <div className="mnav-sound-foot">
            <span>{muted ? "Muted — the level is kept." : "Applies to conflict alerts."}</span>
            <button type="button" className="mnav-sound-test" onClick={onTest} disabled={silent}>
              Test
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
