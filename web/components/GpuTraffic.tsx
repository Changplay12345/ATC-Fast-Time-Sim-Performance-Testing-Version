"use client";

/**
 * GpuTraffic — the flight picture drawn on the GPU with deck.gl, over the
 * Leaflet base map: the route lines and their fix / endpoint dots, the decaying
 * trails, and the aircraft symbols.
 *
 * Why: with Leaflet every airborne aircraft is a DOM marker plus a stack of
 * canvas polylines that React re-creates and Leaflet re-strokes on the CPU
 * every frame, so the cost grows with the traffic count until a whole traffic
 * day stops being smooth. Here:
 *
 *   * Trails are uploaded to the GPU ONCE per data set (every flight's whole
 *     path, timestamped). Each frame only the clock uniform changes; the shader
 *     keeps the [now − decay, now] window of each path and discards the rest.
 *     Nothing is decimated per frame or per traffic count: every sample in a
 *     turn is drawn, straight legs are thinned once at upload (LeafletMap).
 *   * Aircraft are one instanced icon draw (outline + tinted fill), rotated by
 *     heading on the GPU.
 *   * Tags stay real DOM (identical CSS), but as a flat set of absolutely
 *     positioned elements moved by transform, text touched only when it changes
 *     — not a React tooltip per plane re-rendered every frame.
 *
 *   * Route lines + fix / endpoint dots used to be ~26 000 Leaflet canvas paths
 *     for a 500-flight day, all re-stroked on the CPU whenever a pan or zoom
 *     ended (a visible hitch), and blank past the canvas edge mid-drag. On the
 *     GPU they redraw every frame of a pan for nothing. Their hover tooltip and
 *     click popup are recreated here, same content, same hit radii.
 *
 * Stacking is unchanged from the Leaflet drawing: routes above the airspace
 * layers, trails under the live CD&R overlay and the CD&R route glow, aircraft
 * above them, tags above aircraft. The deck canvases take no pointer events;
 * aircraft and route dots are hit-tested from the map's own mouse events, so
 * everything underneath keeps its hover and click.
 *
 * Needs WebGL2; LeafletMap falls back to its Leaflet drawing without it.
 */

import L from "leaflet";
import { useEffect, useRef, useState } from "react";
import { useMap } from "react-leaflet";
import type { Layer } from "@deck.gl/core";
import { IconLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { TripsLayer } from "@deck.gl/geo-layers";
import { DataFilterExtension } from "@deck.gl/extensions";
import { DeckOverlay } from "@/components/DeckOverlay";
import type { Rgba } from "@/lib/displayColors";

/** Panes (created by LeafletMap). Trails sit just under the live-traffic
 *  canvas (CD&R overlay, measure line, 450); aircraft just above the marker
 *  pane (600) and under the tooltip pane (650) that holds the tags. */
export const GPU_TRAIL_PANE = "gpuTrails";
export const GPU_AIRCRAFT_PANE = "gpuAircraft";

let webgl2: boolean | null = null;
/** True when the browser can run deck.gl (WebGL2). Probed once. */
export function supportsWebGL2(): boolean {
  if (webgl2 == null) {
    try {
      webgl2 = !!document.createElement("canvas").getContext("webgl2");
    } catch {
      webgl2 = false;
    }
  }
  return webgl2;
}

// ---------------------------------------------------------------------------
// Data shapes (built by LeafletMap)
// ---------------------------------------------------------------------------
export interface GpuTrail {
  key: string;
  /** [lon, lat] per kept sample. */
  path: [number, number][];
  /** Timeline seconds per vertex (same clock as `simT`). */
  timestamps: number[];
  /** One colour, or one per vertex (the altitude gradient). */
  color: Rgba | Rgba[];
  /** Airborne window on the timeline: the trail shows only inside it, as the
   *  Leaflet trail vanished with its aircraft at touchdown. */
  start: number;
  end: number;
}

export interface GpuAircraft {
  ti: number;
  key: string;
  lat: number;
  lon: number;
  track: number;
  color: Rgba;
  followed: boolean;
  /** Tag lines ("" = none). */
  tag: string;
  airspace: string;
}

/** A filed route's line (drawn when Full Trails shows whole routes). */
export interface GpuRouteLine {
  key: string;
  /** [lon, lat] per kept sample. */
  path: [number, number][];
  /** One colour, or one per vertex (the altitude gradient). */
  color: Rgba | Rgba[];
}

/** A route fix or a route's start/end dot, with what its hover and click show.
 *  Listed in draw order: a later dot is on top, and wins the hit test. */
export interface GpuRoutePoint {
  lat: number;
  lon: number;
  radius: number;
  strokeWidth: number;
  fill: Rgba;
  stroke: Rgba;
  /** Hover target radius (px) — larger than the dot, as the Leaflet hit
   *  circle was. */
  hitRadius: number;
  /** Tooltip / popup content (HTML, already escaped). */
  tooltip: string;
  tooltipOffsetY: number;
  popup: string;
}

/** The "R1", "R2", … pill at a route's start (multi-route sets only). */
export interface GpuRouteBadge {
  lat: number;
  lon: number;
  text: string;
  color: Rgba;
}

export interface GpuRoutes {
  /** null when route lines are not drawn (trail decay replaces them). */
  lines: GpuRouteLine[] | null;
  points: GpuRoutePoint[];
  badges: GpuRouteBadge[];
}

/** The pill's text colour and geometry, matching `.route-index-pill`
 *  (10 px / 800 on the route colour, 7×2 px padding) and the divIcon anchor
 *  (the box sat 6 px right of the point and 10 px above it). */
const BADGE_TEXT_RGBA: Rgba = [6, 40, 61, 255]; // #06283d
const BADGE_PADDING: [number, number, number, number] = [7, 2, 7, 2];
const BADGE_OFFSET: [number, number] = [6, -10];

/**
 * The Leaflet trail keeps [now − decay, now] at full opacity and cuts hard at
 * the window edge. deck's TripsLayer only cuts the tail when it is also fading
 * it, so this variant swaps in the hard cut (WebGL; WebGPU keeps the stock one).
 */
class DecayTrailLayer extends TripsLayer<GpuTrail> {
  static layerName = "DecayTrailLayer";
  getShaders() {
    const shaders = super.getShaders();
    if (this.context.device.type !== "webgpu") {
      shaders.inject = {
        ...shaders.inject,
        "fs:DECKGL_FILTER_COLOR": `\
if (vTime > trips.currentTime || vTime < trips.currentTime - trips.trailLength) {
  discard;
}
`,
      };
    }
    return shaders;
  }
}

// ---------------------------------------------------------------------------
// Aircraft symbol: the same plane path as the Leaflet divIcon. The dark
// outline is its own silhouette (the path stroked fat) drawn under a tinted
// fill, since a masked icon takes a single colour.
// ---------------------------------------------------------------------------
const PLANE_PATH =
  "M12 2 L14 10 L22 14 L22 16 L14 13 L13 20 L16 22 L16 23 L12 22 L8 23 L8 22 L11 20 L10 13 L2 16 L2 14 L10 10 Z";
const ICON_PX = 64; // raster size; drawn at 20 px, so crisp on HiDPI
/** The plane rasterised to a PNG data URL (canvas + Path2D — deck never has to
 *  decode an SVG). `strokePx` > 0 fattens it into the outline silhouette. */
function planePng(strokeUnits: number): string {
  const c = document.createElement("canvas");
  c.width = c.height = ICON_PX;
  // A CPU-backed canvas: `toDataURL` on a GPU-backed one is a readback that
  // waits for the GPU queue — 175 ms once, when it happened to coincide with
  // the shader compiles of a big import.
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.scale(ICON_PX / 24, ICON_PX / 24);
  const path = new Path2D(PLANE_PATH);
  ctx.fillStyle = ctx.strokeStyle = "#fff";
  ctx.fill(path);
  if (strokeUnits > 0) {
    ctx.lineWidth = strokeUnits;
    ctx.lineJoin = "round";
    ctx.stroke(path);
  }
  return c.toDataURL("image/png");
}
interface PlaneIcon {
  id: string;
  url: string;
  width: number;
  height: number;
  anchorY: number;
  mask: boolean;
}
let icons: { outline: PlaneIcon; fill: PlaneIcon } | null = null;
function planeIcons() {
  if (!icons) {
    const base = { width: ICON_PX, height: ICON_PX, anchorY: ICON_PX / 2, mask: true };
    icons = {
      outline: { ...base, id: "plane-outline", url: planePng(2.4) },
      fill: { ...base, id: "plane-fill", url: planePng(0) },
    };
  }
  return icons;
}
const OUTLINE_RGBA: Rgba = [15, 23, 42, 255]; // #0f172a, the divIcon stroke
const CASING_RGBA: Rgba = [15, 23, 42, 102]; // #0f172a @ 0.4, the trail casing
const ROUTE_CASING_RGBA: Rgba = [15, 23, 42, 115]; // #0f172a @ 0.45, the route casing
const HIT_RADIUS_PX = 12;
/** Every layer type is kept mounted with no data while it has nothing to
 *  draw. deck compiles a layer's shaders when the layer first exists, and a
 *  traffic day's import used to pay for all of them (~340 ms, blocking) in
 *  the same frame it built the geometry. Now they compile at page load. */
const EMPTY: never[] = [];

// ---------------------------------------------------------------------------
// Tags: flat DOM, positioned in layer coordinates inside the tooltip pane so
// they pan with the map for free and animate with Leaflet's zoom like markers.
// ---------------------------------------------------------------------------
interface TagItem {
  wrap: HTMLDivElement;
  box: HTMLDivElement;
  line1: HTMLDivElement;
  line2: HTMLDivElement;
  ring: HTMLSpanElement | null;
  tag: string;
  airspace: string;
  latlng: L.LatLng;
}

class TagLayer {
  private root: HTMLDivElement;
  private items = new Map<string, TagItem>();

  constructor(private map: L.Map) {
    this.root = L.DomUtil.create("div", "gpu-tag-layer");
    map.getPane("tooltipPane")!.appendChild(this.root);
    map.on("zoomanim", this.onZoomAnim, this);
    map.on("zoomend viewreset", this.reposition, this);
  }

  destroy() {
    this.map.off("zoomanim", this.onZoomAnim, this);
    this.map.off("zoomend viewreset", this.reposition, this);
    this.root.remove();
    this.items.clear();
  }

  update(list: GpuAircraft[]) {
    const seen = new Set<string>();
    for (const a of list) {
      seen.add(a.key);
      let it = this.items.get(a.key);
      if (!it) {
        const wrap = L.DomUtil.create("div", "gpu-tag leaflet-zoom-animated", this.root);
        const box = L.DomUtil.create("div", "leaflet-tooltip aircraft-tag gpu-tag-box", wrap);
        const line1 = L.DomUtil.create("div", "", box);
        const line2 = L.DomUtil.create("div", "aircraft-tag-airspace", box);
        it = { wrap, box, line1, line2, ring: null, tag: "\0", airspace: "\0", latlng: L.latLng(a.lat, a.lon) };
        this.items.set(a.key, it);
      }
      if (it.tag !== a.tag) {
        it.tag = a.tag;
        it.line1.textContent = a.tag;
        it.line1.style.display = a.tag ? "" : "none";
      }
      if (it.airspace !== a.airspace) {
        it.airspace = a.airspace;
        it.line2.textContent = a.airspace;
        it.line2.style.display = a.airspace ? "" : "none";
      }
      it.box.style.display = a.tag || a.airspace ? "" : "none";
      if (a.followed && !it.ring) {
        // Under the tag box, as the marker's ring sat under the tooltip pane.
        it.ring = L.DomUtil.create("span", "aircraft-ring");
        it.wrap.insertBefore(it.ring, it.box);
      } else if (!a.followed && it.ring) {
        it.ring.remove();
        it.ring = null;
      }
      it.latlng = L.latLng(a.lat, a.lon);
      // Mid zoom-animation the element is already travelling to its target.
      if (!(this.map as unknown as { _animatingZoom: boolean })._animatingZoom) {
        L.DomUtil.setPosition(it.wrap, this.map.latLngToLayerPoint(it.latlng).round());
      }
    }
    for (const [key, it] of this.items) {
      if (!seen.has(key)) {
        it.wrap.remove();
        this.items.delete(key);
      }
    }
  }

  private reposition() {
    for (const it of this.items.values()) {
      L.DomUtil.setPosition(it.wrap, this.map.latLngToLayerPoint(it.latlng).round());
    }
  }

  private onZoomAnim(e: L.ZoomAnimEvent) {
    const m = this.map as unknown as {
      _latLngToNewLayerPoint: (ll: L.LatLng, z: number, c: L.LatLng) => L.Point;
    };
    for (const it of this.items.values()) {
      L.DomUtil.setPosition(it.wrap, m._latLngToNewLayerPoint(it.latlng, e.zoom, e.center).round());
    }
  }
}

// ---------------------------------------------------------------------------
// The component
// ---------------------------------------------------------------------------
interface Props {
  /** Route lines + fix / endpoint dots; null = none. */
  routes: GpuRoutes | null;
  /** Every flight's timestamped trail; null = no decaying trails drawn. */
  trails: GpuTrail[] | null;
  trailDecaySec: number;
  trailWeight: number;
  /** Hide each trail outside its flight's airborne window ("all" playback). */
  airborneOnly: boolean;
  simT: number;
  aircraft: GpuAircraft[];
  onAircraftClick?: (index: number) => void;
  onAircraftHover?: (index: number | null) => void;
}

export default function GpuTraffic({
  routes,
  trails,
  trailDecaySec,
  trailWeight,
  airborneOnly,
  simT,
  aircraft,
  onAircraftClick,
  onAircraftHover,
}: Props) {
  const map = useMap();
  const trailDeck = useRef<DeckOverlay | null>(null);
  const aircraftDeck = useRef<DeckOverlay | null>(null);
  const routesRef = useRef(routes);
  routesRef.current = routes;
  const tags = useRef<TagLayer | null>(null);
  /** The aircraft as last drawn, in draw order — what the pointer hit-tests. */
  const drawn = useRef<GpuAircraft[]>([]);
  const cb = useRef({ onAircraftClick, onAircraftHover });
  cb.current = { onAircraftClick, onAircraftHover };
  // One extension instance: a new one per render would rebuild the shaders.
  const filterExt = useRef(new DataFilterExtension({ filterSize: 2 }));

  // Mount: two deck canvases (so the CD&R overlay can sit between trails and
  // aircraft), the tag layer, and aircraft hit-testing off the map's mouse
  // events.
  //
  // The decks start one frame after mount, so React StrictMode's throwaway
  // mount → unmount in development never spins up (and tears straight down) a
  // GPU context.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const make = (pane: string) => {
      const layer = new DeckOverlay({ layers: [], useDevicePixels: true }, pane);
      layer.addTo(map);
      return layer;
    };
    const startRaf = requestAnimationFrame(() => {
      trailDeck.current = make(GPU_TRAIL_PANE);
      aircraftDeck.current = make(GPU_AIRCRAFT_PANE);
      tags.current = new TagLayer(map);
      setReady(true); // re-render so the layer effect fills them, even paused
    });

    const container = map.getContainer();
    const animating = () =>
      !!(map as unknown as { _animatingZoom?: boolean })._animatingZoom;
    let hovered: number | null = null;
    let down: { x: number; y: number } | null = null;

    // Aircraft: the 12 px circle the Leaflet hit marker had. A few hundred
    // projections per mouse event is microseconds, and it needs no GPU picking
    // pass. Topmost first (the followed aircraft draws last).
    const pickAircraft = (m: L.Point): number | null => {
      const list = drawn.current;
      for (let i = list.length - 1; i >= 0; i--) {
        const p = map.latLngToContainerPoint([list[i].lat, list[i].lon]);
        const dx = p.x - m.x;
        const dy = p.y - m.y;
        if (dx * dx + dy * dy <= HIT_RADIUS_PX * HIT_RADIUS_PX) return list[i].ti;
      }
      return null;
    };

    // Route dots: thousands of them, so their screen positions are projected
    // once per view (re-done lazily after the map moves) and a hover is then a
    // plain distance scan.
    let dotXY: L.Point[] | null = null;
    let dotXYFor: GpuRoutePoint[] | null = null;
    const invalidateDots = () => {
      dotXY = null;
    };
    map.on("move zoomend viewreset resize", invalidateDots);
    const pickDot = (m: L.Point): GpuRoutePoint | null => {
      const pts = routesRef.current?.points ?? [];
      if (!dotXY || dotXYFor !== pts) {
        dotXY = pts.map((p) => map.latLngToContainerPoint([p.lat, p.lon]));
        dotXYFor = pts;
      }
      for (let i = pts.length - 1; i >= 0; i--) {
        const dx = dotXY[i].x - m.x;
        const dy = dotXY[i].y - m.y;
        const r = pts[i].hitRadius;
        if (dx * dx + dy * dy <= r * r) return pts[i];
      }
      return null;
    };

    // The dot's hover label: one Leaflet tooltip, moved and re-filled, and
    // "sticky" (it follows the pointer) like the ones on the old hit circles.
    const tip = L.tooltip({ direction: "top", offset: [0, -7] });
    let tipFor: GpuRoutePoint | null = null;
    const hideTip = () => {
      if (tipFor) {
        map.closeTooltip(tip);
        tipFor = null;
      }
    };
    const showTip = (p: GpuRoutePoint, e: MouseEvent) => {
      if (tipFor !== p) {
        tip.options.offset = L.point(0, p.tooltipOffsetY);
        tip.setContent(p.tooltip);
        tipFor = p;
      }
      tip.setLatLng(map.mouseEventToLatLng(e));
      if (!map.hasLayer(tip)) map.openTooltip(tip);
    };

    const setHover = (ti: number | null, pointer: boolean) => {
      container.classList.toggle("gpu-aircraft-hover", pointer);
      if (ti === hovered) return;
      hovered = ti;
      cb.current.onAircraftHover?.(ti);
    };
    // Synchronous, so a hit can stop the event before the Leaflet canvas under
    // the GPU layer reacts to it — the old dots sat on top of that canvas and
    // took the hover from whatever was beneath.
    const onMove = (e: MouseEvent) => {
      if (e.buttons !== 0 || animating()) {
        hideTip();
        return; // a drag, or mid-zoom
      }
      const m = map.mouseEventToContainerPoint(e);
      const ti = pickAircraft(m);
      if (ti != null) {
        hideTip();
        setHover(ti, true);
        return;
      }
      const dot = pickDot(m);
      setHover(null, !!dot);
      if (dot) {
        showTip(dot, e);
        e.stopPropagation();
      } else {
        hideTip();
      }
    };
    const onLeave = () => {
      hideTip();
      setHover(null, false);
    };
    const onDown = (e: MouseEvent) => {
      down = { x: e.clientX, y: e.clientY };
    };
    // Capture phase, so a plane or a dot wins over whatever is drawn under it,
    // as the plane marker and the dots' hit circles did.
    const onClick = (e: MouseEvent) => {
      if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return; // a drag
      if (animating()) return;
      const m = map.mouseEventToContainerPoint(e);
      const ti = pickAircraft(m);
      if (ti != null) {
        e.stopPropagation();
        cb.current.onAircraftClick?.(ti);
        return;
      }
      const dot = pickDot(m);
      if (dot) {
        e.stopPropagation();
        hideTip();
        L.popup().setLatLng(map.mouseEventToLatLng(e)).setContent(dot.popup).openOn(map);
      }
    };
    container.addEventListener("mousemove", onMove, true);
    container.addEventListener("mouseleave", onLeave);
    container.addEventListener("mousedown", onDown, true);
    container.addEventListener("click", onClick, true);

    return () => {
      cancelAnimationFrame(startRaf);
      map.off("move zoomend viewreset resize", invalidateDots);
      hideTip();
      container.removeEventListener("mousemove", onMove, true);
      container.removeEventListener("mouseleave", onLeave);
      container.removeEventListener("mousedown", onDown, true);
      container.removeEventListener("click", onClick, true);
      container.classList.remove("gpu-aircraft-hover");
      trailDeck.current?.remove();
      aircraftDeck.current?.remove();
      tags.current?.destroy();
      trailDeck.current = aircraftDeck.current = null;
      tags.current = null;
      setReady(false);
    };
  }, [map]);

  // Every render (i.e. every clock tick): new layer descriptors. deck diffs
  // them — the trail geometry is only re-uploaded when `trails` changes; per
  // frame it is two uniforms (clock, airborne filter).
  useEffect(() => {
    if (!ready) return;
    planeIcons(); // rasterised now, at page load, not when the first plane appears
    const trailLayers: Layer[] = [];
    // Route lines (whole-route mode), then the dots, then the decaying trails —
    // the Leaflet order: dots over lines, trails over dots.
    {
      const common = {
        data: routes?.lines ?? EMPTY,
        getPath: (d: GpuRouteLine) => d.path,
        widthUnits: "pixels" as const,
        capRounded: true,
        jointRounded: true,
        updateTriggers: { getWidth: trailWeight },
      };
      trailLayers.push(
        new PathLayer<GpuRouteLine>({
          ...common,
          id: "gpu-route-casing",
          getColor: ROUTE_CASING_RGBA,
          getWidth: trailWeight + 2,
        }),
        new PathLayer<GpuRouteLine>({
          ...common,
          id: "gpu-route",
          getColor: (d) => d.color,
          opacity: 0.95,
          getWidth: trailWeight,
        }),
      );
    }
    {
      trailLayers.push(
        new ScatterplotLayer<GpuRoutePoint>({
          id: "gpu-route-dots",
          data: routes?.points ?? EMPTY,
          getPosition: (d) => [d.lon, d.lat],
          radiusUnits: "pixels",
          lineWidthUnits: "pixels",
          stroked: true,
          filled: true,
          // Leaflet centres the stroke on the radius; deck draws it inside.
          getRadius: (d) => d.radius + d.strokeWidth / 2,
          getLineWidth: (d) => d.strokeWidth,
          getFillColor: (d) => d.fill,
          getLineColor: (d) => d.stroke,
        }),
      );
    }
    // Route index badges: one DOM marker per route was 2 000 composited
    // elements on a traffic day — every zoom animated them all (30 ms of
    // style recalc a frame) and every drag composited them. Text on the GPU
    // costs nothing per frame.
    {
      trailLayers.push(
        new TextLayer<GpuRouteBadge>({
          id: "gpu-route-badges",
          data: routes?.badges ?? EMPTY,
          getPosition: (d) => [d.lon, d.lat],
          getText: (d) => d.text,
          getColor: BADGE_TEXT_RGBA,
          getSize: 10,
          sizeUnits: "pixels",
          fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
          fontWeight: 800,
          getTextAnchor: "start",
          getAlignmentBaseline: "bottom",
          getPixelOffset: BADGE_OFFSET,
          background: true,
          getBackgroundColor: (d) => d.color,
          backgroundPadding: BADGE_PADDING,
        }),
      );
    }
    {
      const common = {
        data: trails && trailDecaySec > 0 ? trails : EMPTY,
        getPath: (d: GpuTrail) => d.path,
        getTimestamps: (d: GpuTrail) => d.timestamps,
        widthUnits: "pixels" as const,
        capRounded: true,
        jointRounded: true,
        fadeTrail: false,
        trailLength: trailDecaySec,
        currentTime: simT,
        extensions: [filterExt.current],
        getFilterValue: (d: GpuTrail) => [d.start, d.end],
        filterRange: [
          [-1e9, simT],
          [simT, 1e9],
        ] as [number, number][],
        filterEnabled: airborneOnly,
      };
      trailLayers.push(
        new DecayTrailLayer({
          ...common,
          id: "gpu-trail-casing",
          getColor: CASING_RGBA,
          getWidth: trailWeight + 2,
          updateTriggers: { getWidth: trailWeight },
        }),
        new DecayTrailLayer({
          ...common,
          id: "gpu-trail",
          getColor: (d: GpuTrail) => d.color,
          opacity: 0.95,
          getWidth: trailWeight,
          updateTriggers: { getWidth: trailWeight },
        }),
      );
    }
    trailDeck.current?.setProps({ layers: trailLayers });

    // The followed aircraft draws last, so it is on top (the marker's
    // zIndexOffset).
    const ordered = aircraft.some((a) => a.followed)
      ? [...aircraft.filter((a) => !a.followed), ...aircraft.filter((a) => a.followed)]
      : aircraft;
    const iconCommon = {
      data: ordered,
      getPosition: (d: GpuAircraft) => [d.lon, d.lat] as [number, number],
      // deck turns counter-clockwise; track is clockwise from north.
      getAngle: (d: GpuAircraft) => -Math.round(d.track),
      sizeUnits: "pixels" as const,
      getSize: 20,
    };
    drawn.current = ordered;
    aircraftDeck.current?.setProps({
      layers: [
        new IconLayer<GpuAircraft>({
          ...iconCommon,
          id: "gpu-aircraft-outline",
          getIcon: () => planeIcons().outline,
          getColor: OUTLINE_RGBA,
        }),
        new IconLayer<GpuAircraft>({
          ...iconCommon,
          id: "gpu-aircraft-fill",
          getIcon: () => planeIcons().fill,
          getColor: (d: GpuAircraft) => d.color,
        }),
      ],
    });

    tags.current?.update(aircraft);
  });

  return null;
}
