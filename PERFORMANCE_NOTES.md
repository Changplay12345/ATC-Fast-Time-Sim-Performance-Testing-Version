# Map performance work: notes and reusable procedure

This covers the work done on 2026-09-27 to make playback of 500 to 2,000
simulated flights smooth in the web map. Nothing was removed and no data was
thinned. It records what was slow, why, how each part was fixed, the bugs found
along the way, the user's feedback, and a step-by-step procedure for doing the
same in another project.

Stack at the time: Next.js 14 (dev mode, React StrictMode), React 18,
react-leaflet 4.2.1, Leaflet 1.9.4, deck.gl **9.3.11** (pinned) and a FastAPI
backend. The test machine was a Ryzen 7 9800X3D, an RTX 4070 and a 1920×1080
screen at 180 Hz.

---

## 1. Results

| Scenario | Before | After |
|---|---|---|
| 500 flights, playback (first round, Leaflet only, steps 1–2) | 8.9 fps, 111 ms CPU per frame | 98 fps, 10 ms |
| 500 flights, 10-min decaying trails and tags | 60 fps, 16.7 ms | **177 fps**, 2.6 ms |
| 1,990 flights, same settings | 16 fps, 63 ms, 121 freezes in 15 s | **116 fps**, 7.6 ms, no freezes |
| Panning with 500 flights | 15 frames over 50 ms, 4 long tasks of 55–71 ms, worst frame 450 ms | no long tasks, worst frame 22 ms, p99 5.7 ms |
| Panning, visible browser window, real GPU, 180 Hz | — | about 170 fps, median frame 5.6 ms |

All numbers are from **development mode**. A production build
(`npm run build`, then `npm start`) is faster still. The 500-flight and
1,990-flight comparisons were run with a game using about 4 CPU cores in the
background. Both versions ran under the same conditions, so the comparison is
fair, but absolute numbers on an idle machine are higher.

---

## 2. Why it lagged (root causes)

A stronger server would not have helped. Trajectories are computed on the server
once, when Generate is pressed. The lag was entirely the **browser drawing each
frame**. About 60 times a second the playback clock triggered all of the
following, from most to least expensive:

1. **Static route lines were redrawn every frame.** About 12,000 route line
   pieces shared one Leaflet canvas (`preferCanvas`) with the moving trails and
   aircraft. When anything on a canvas moves, that area is repainted, and the
   trails cover the whole map, so all the routes were repainted every frame even
   though they never change.
2. **Every aircraft icon was rebuilt every frame.** `planeIcon(...)` made a new
   `L.divIcon` each render, so Leaflet removed and re-created every plane's DOM
   (icon and label) 60 times a second, which forced constant layout work.
3. **Each altitude-coloured trail segment was its own Leaflet polyline**, rebuilt
   every frame.
4. **The whole app re-rendered every frame.** Each clock tick re-rendered all of
   `MapApp` (about 5,400 lines) and every flight, including closed panels.
5. **One CPU core did all the work.** The GPU sat idle.

Streaming video from a server was ruled out because it loses interaction:
clicking planes, hover details, following a flight and the measure tool.

---

## 3. The fixes, in the order they were applied

The plan offered to the user, in order of cost and benefit:

| # | Change | Effect |
|---|---|---|
| 1 | Give the static routes their own canvas, separate from the moving layer | Removes about 80% of per-frame drawing |
| 2 | Create each plane icon once, then only move and rotate it | Removes 500+ DOM rebuilds per frame |
| 3 | Keep per-frame work out of React; update panels less often | Stops whole-app re-renders |
| 4 | Draw all trails in one pass instead of thousands of line objects | Cheap trails |
| 5 | Draw aircraft and trails on the GPU (deck.gl) | Handles 2,000–10,000 flights |

Steps 1, 2 and 5 were built, and step 3 was done as targeted re-render fixes
(section 3.4). Step 5 made step 4 unnecessary.

### 3.1 Step 1: separate canvases (Leaflet panes)

- Leaflet repaints a whole canvas when anything on it changes, so **anything that
  moves every frame must not share a canvas with things that don't**.
- Custom panes were created *before* any layer that names them, with fixed
  z-indexes. Moving layers got `pane={LIVE_PANE}`, which gives them their own
  canvas renderer.
- The live pane has `pointer-events: none`, so the mouse passes through to the
  route and fix layers underneath.
- **Rule for future code:** any layer that changes every frame must go on the
  live pane (or the GPU). If someone adds an animated layer without it, the lag
  comes back straight away.

Final pane order (higher is on top):

| Pane | z-index | Contents |
|---|---|---|
| overlayPane | 400 | Leaflet canvas: airspace, airways, static vectors (renderer `L.canvas({ padding: 0.5 })`) |
| `gpuTrails` | 445 | deck.gl: route lines, fix and endpoint dots, decaying trails |
| `liveTraffic` | 450 | Leaflet canvas: conflict overlay, measure line; `pointer-events: none` |
| `cdrRoutes` | 460 | SVG: CD&R preview and applied-fix routes |
| markerPane | 600 | Leaflet markers (airports and so on) |
| `gpuAircraft` | 605 | deck.gl: aircraft icons |
| tooltipPane | 650 | DOM flight tags (`TagLayer`) |

### 3.2 Step 2: build each plane icon once

- The icon is created once per flight and memoised. Each frame only moves the
  marker and rotates an inner element by writing `style.transform` on the
  `.aircraft-rot` element directly, outside React.
- The plane itself now takes the click and hover, replacing an invisible click
  circle underneath it. The hit area was kept about the same: a 20 px icon
  padded out to 24 px, where the old circle was 24 px.
- Side effect, and a bug fix: the pulsing ring on a followed aircraft now
  actually pulses. It used to restart every frame because the icon was rebuilt.

### 3.3 Step 5: GPU drawing with deck.gl

Files: `web/components/GpuTraffic.tsx` and `web/components/DeckOverlay.ts`.

- **Packages:** `@deck.gl/core`, `@deck.gl/layers`, `@deck.gl/geo-layers` and
  `@deck.gl/extensions`, **all pinned to exactly 9.3.11**. See bug B3 for why.
- **Layers used:**
  - `TripsLayer` subclass `DecayTrailLayer` for decaying trails, with a shader
    injection for a hard cut (below).
  - `DataFilterExtension({ filterSize: 2 })`, keyed on each flight's
    `[start, end]`, so only airborne flights are drawn. This is done on the GPU,
    with no per-frame filtering in JavaScript.
  - `IconLayer` for the aircraft: two layers, a dark outline silhouette under a
    tinted fill, because a mask icon takes only one colour. The icons are
    rasterised to PNG data URLs with a canvas and `Path2D`, so deck.gl never has
    to decode an SVG.
  - `PathLayer` for the whole route lines and `ScatterplotLayer` for the fix and
    endpoint dots.
- **Decimation that keeps turns:** in a route line, a point is kept if the track
  turned by at least 1° since the last kept point, or every 6 samples on a
  straight. Turns stay at full resolution.
- **Hard-cut decaying trails:** the stock `TripsLayer` fades the tail. The app
  needed full opacity with a hard cut at the decay window:

  ```ts
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
  ```

- **Mouse hits tested on the CPU, not with GPU picking:** deck.gl's IconLayer
  picking threw a uniform-block error (bug B4). Instead:
  - Aircraft screen positions are cached as container points.
  - Clicks and mouse moves are hit-tested with a radius: 12 px for aircraft,
    13 px for fixes, 20 px for endpoints.
  - The handlers run in the capture phase and call `stopPropagation()` on a hit,
    so Leaflet doesn't also handle the click.
- **Tags stay in the DOM** (`TagLayer`, tooltip pane), so their styling is
  unchanged. Their text only changes when the value changes.
- **Fallback:** `supportsWebGL2()` is checked once. Without WebGL2 the Leaflet
  path from steps 1–2 is kept intact (`!gpu` branches).
- `web/lib/displayColors.ts` gained `cssToRgba()` and the `Rgba` type, with tests,
  to convert the app's CSS colours to deck.gl colour arrays.

### 3.4 React re-render fixes (in place of step 3)

A profile after step 5 showed drawing at about 1% of frame time. The rest was
React:

| Problem | Fix | File |
|---|---|---|
| **GeneratorPanel infinite update loop** ("Maximum update depth exceeded", about 50 per second from page load, even with no flights; re-ran the departure-conflict check each time) | Memoised `liveActive` and `allDrafts` so they only change when a plan really changes. Warnings went from 246 per 5 s to 0. | `GeneratorPanel.tsx` |
| An inline callback in `MapApp` defeated `GeneratorPanel`'s `memo`, so all 1,990 plan tabs re-rendered every frame (about 29% of frame time) | Made it a stable `useCallback` | `MapApp.tsx` |
| FilterPanel ranked every flight every frame, even while closed (about 6%) | Skip the work while closed; the search text is kept | `FilterPanel.tsx` |
| Flight durations re-parsed from timestamps every frame (about 7%) | Cached per trajectory in a `WeakMap` | `useSimPlayback.ts` |
| The per-frame aircraft pass ran filter checks on flights still on the ground | Check airborne time first | `LeafletMap.tsx` |

### 3.5 Smooth pan and zoom

Feedback after step 5: panning stuttered, and parts of the map were half gone
until later.

- **Traffic missing during a drag (fixed):** the `deck.gl-leaflet` package only
  re-synced deck.gl when a pan *ended*. Its canvas was exactly one screen big and
  rode along with the map pane, so anything dragged in from off-screen was blank
  until mouse-up. It was replaced with a custom `DeckOverlay` Leaflet layer
  (full code in section 5.1) that:
  - re-positions and redraws on **every** `move`, coalesced to one sync per
    animation frame;
  - applies a CSS transform during the zoom animation, like Leaflet's own vector
    renderer, then does a full sync at `zoomend`;
  - uses viewState zoom `map.getZoom() - 1`, because deck.gl's web-mercator
    tiles are 512 px and Leaflet's are 256 px.
- **Stutter when a drag ended (fixed):** about 26,000 route lines, fix dots and
  endpoint dots were redrawn on the CPU by Leaflet after every pan or zoom. They
  were moved to the GPU (`PathLayer` and `ScatterplotLayer`) with the same
  colours, sizes, tooltips, popups and hover radius.
- **Blank strips on static layers (fixed):** the Leaflet vector canvas only draws
  10% past the screen edge by default. The renderer is now
  `L.canvas({ padding: 0.5 })`, half a screen each side.
- **Tiles re-downloaded when panning back (fixed):** `keepBuffer={6}` on both tile
  layers.
- **Only about 60–76% of the screen rendered (fixed; last bug found):**
  - The generator side panel narrows the map, for example to 1460 of 1920 px.
  - Leaflet only re-measures its size on a *window* resize. When the panel
    closed, Leaflet kept the old width, so tiles, the vector canvas and the
    deck.gl canvas were drawn only across the left part of the screen.
  - While dragging, the stale content briefly came into view, then got cut off
    again on the next sync, which also felt like stutter.
  - Fix: a `ResizeObserver` on the map container that calls `invalidateSize` (see
    `SizeWatcher` in section 5.2).
- **Still expected:** map areas never viewed before can take a moment to appear,
  because the tiles come from Esri over the internet.

### 3.6 Features added on request

- **Mute button** for the conflict-alert sounds: `MapApp` holds `soundMuted`
  (saved as `atc.soundMuted` in localStorage) and calls
  `playAlert(severity, soundMutedRef.current)`. It's a nav button with the
  `volume` and `volume-off` icons.
- **FPS meter in the style of the MSI Afterburner overlay** (`FpsMeter.tsx`,
  toggle saved as `atc.fpsMeter`):
  - Shows live FPS, frame time, 1% low and min/avg/max, with a 30-second graph
    (a sample every 100 ms, 300 samples).
  - Reference lines at 30, 60, 120 and 240 fps. Green at 55 fps and up, yellow
    at 30 and up, red below.
  - Runs its **own `requestAnimationFrame` loop that writes straight to a canvas
    and text nodes, outside React**, so measuring doesn't cost frames, and a
    stall shows up as a dip rather than a frozen meter.
  - Ignores the mouse.
- `web/lib/mapPrefs.ts` gained `loadFlag` and `saveFlag` for boolean preferences.

### 3.7 Test data

- `dummy_data/fts_500_flights_20260927.csv`: 520 realistic flights built from the
  project's own data. The import CSV header is
  `callsign,actype,adep,ades,eobt,rfl,gs,dep_rwy,arr_rwy,sid,star,approach,route`.
  - Mix: 170 domestic, 128 arrivals, 127 departures, 95 overflights.
  - 261 city pairs, 93 airlines, 31 aircraft types.
- Generator: `python scripts/make_fts500_flights.py --flights 800 --seed 5` (run
  from the venv; about 35 s). It calls the `api.server` functions in-process.
- `web/lib/fts500Dummy.test.ts` checks that the file parses through the real
  importer.
- Stress test: `dummy_data/fts_traffic_20260709Star.csv` (1,990 flights).

---

## 4. Bugs and gotchas hit along the way

| # | Problem | Cause | Fix |
|---|---|---|---|
| B1 | FastAPI `TestClient` unavailable | `httpx` not installed | Called the server functions directly, in-process |
| B2 | `deck.gl-leaflet` export or type problems | Package packaging | Replaced by the custom `DeckOverlay` (it also had the sync-on-moveend problem, 3.5) |
| B3 | **WebGL context lost** on deck.gl 9.4.0 when drawing aircraft icons (RTX 4070) | IconLayer mipmap generation in 9.4.0 | **Pinned all deck.gl packages to exactly 9.3.11** (no `^`) |
| B4 | IconLayer GPU picking threw a uniform-block error | deck.gl picking bug | CPU hit-testing (3.3) |
| B5 | TripsLayer tail faded instead of cutting off | Stock shader fades | `DecayTrailLayer` shader injection (3.3) |
| B6 | Custom `L.Layer` ignored its `pane` option | `L.Layer` has no `initialize()`, so options passed to the constructor are dropped | Call `L.setOptions(this, { pane })` in the constructor |
| B7 | GeneratorPanel "Maximum update depth exceeded" | Arrays rebuilt every render fed back into state | Memoisation (3.4) |
| B8 | Dev servers died after `npm uninstall` | Next dev server loses `node_modules` mid-run | Restart the dev servers after any install or uninstall |
| B9 | Only 60–76% of the map drawn; stutter while dragging | Leaflet's cached size is stale after a non-window layout change | `ResizeObserver` and `invalidateSize` (5.2) |
| B10 | Shell heredoc quoting broke multi-line edits on Windows | Git Bash quoting | Write edits as small `.py` scripts instead |
| B11 | Python reading a file failed with a `cp874` decode error | Windows Thai locale default encoding | Always `open(..., encoding="utf-8")` |
| B12 | Benchmark run showed about 1 fps | Headed test window was hidden or occluded; Chrome throttles `requestAnimationFrame` to about 1 Hz | Keep the test window visible; discard such runs |

### Open issues (not fixed)

- **Duplicate React key** `${kp}-${w.ident}` when a route passes the same fix twice
  (for example HVN28 via VAPVU). This only affects the Leaflet fallback path.
  It's a one-line fix: add the index to the key.
- **Not tested:** the non-WebGL2 fallback on a machine that really lacks WebGL2
  (it was only forced on for the comparison), touch devices (tapping a plane on
  a tablet), and browsers other than Chrome.

---

## 5. Reusable code

### 5.1 `DeckOverlay`: deck.gl in a Leaflet pane, synced every frame

```ts
import L from "leaflet";
import { Deck, type DeckProps } from "@deck.gl/core";

type LeafletMapInternals = L.Map & {
  _animatingZoom?: boolean;
  _getMapPanePos: () => L.Point;
};

function viewState(map: L.Map) {
  const c = map.getCenter();
  // deck's web-mercator tiles are 512 px, Leaflet's 256: one zoom level apart.
  return { longitude: c.lng, latitude: c.lat, zoom: map.getZoom() - 1, pitch: 0, bearing: 0 };
}

export class DeckOverlay extends L.Layer {
  private deckProps: DeckProps;
  private container: HTMLDivElement | null = null;
  private deck: Deck | null = null;
  private raf = 0;

  constructor(props: DeckProps, pane: string) {
    super();
    L.setOptions(this, { pane }); // L.Layer drops constructor options otherwise
    this.deckProps = props;
  }

  onAdd(map: L.Map): this {
    const pane = this.getPane();
    if (!pane) return this;
    const el = L.DomUtil.create("div", "leaflet-layer gpu-deck");
    if (map.options.zoomAnimation && L.Browser.any3d) L.DomUtil.addClass(el, "leaflet-zoom-animated");
    pane.appendChild(el);
    this.container = el;
    this.deck = new Deck({
      ...this.deckProps, parent: el, controller: false,
      style: { zIndex: "auto" }, viewState: viewState(map),
    });
    this.sync();
    return this;
  }

  onRemove(): this {
    cancelAnimationFrame(this.raf); this.raf = 0;
    this.deck?.finalize(); this.deck = null;
    this.container?.remove(); this.container = null;
    return this;
  }

  getEvents() {
    return {
      move: this.scheduleSync,
      zoomend: this.sync, viewreset: this.sync, resize: this.sync,
      zoomanim: this.animZoom as L.LeafletEventHandlerFn,
    };
  }

  setProps(props: DeckProps): void {
    Object.assign(this.deckProps, props);
    this.deck?.setProps(props);
  }

  private scheduleSync(): void {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => { this.raf = 0; this.sync(); });
  }

  private sync(): void {
    const map = this._map as LeafletMapInternals | undefined;
    const el = this.container;
    if (!map || !el || !this.deck) return;
    if (map._animatingZoom) return; // the CSS transform owns the canvas meanwhile
    const size = map.getSize();
    el.style.width = `${size.x}px`;
    el.style.height = `${size.y}px`;
    L.DomUtil.setPosition(el, map._getMapPanePos().multiplyBy(-1)); // back over the viewport
    this.deck.setProps({ viewState: viewState(map) });
    this.deck.redraw("leaflet-sync");
  }

  private animZoom(e: L.ZoomAnimEvent): void {
    const map = this._map, el = this.container;
    if (!map || !el) return;
    const scale = map.getZoomScale(e.zoom, map.getZoom());
    const position = L.DomUtil.getPosition(el);
    const viewHalf = map.getSize().multiplyBy(0.5);
    const centerOffset = map.project(e.center, e.zoom).subtract(map.project(map.getCenter(), e.zoom));
    const topLeft = viewHalf.multiplyBy(-scale).add(position).add(viewHalf).subtract(centerOffset);
    if (L.Browser.any3d) L.DomUtil.setTransform(el, topLeft, scale);
    else L.DomUtil.setPosition(el, topLeft);
  }
}
```

Use one overlay per pane (here one below the markers for trails and routes, and
one above them for aircraft). The pane needs `pointer-events: none`, with mouse
hits tested on the CPU.

### 5.2 `SizeWatcher`: keep Leaflet's size in step with its container

```tsx
function SizeWatcher() {
  const map = useMap();
  useEffect(() => {
    const el = map.getContainer();
    let f = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(f);
      f = requestAnimationFrame(() => {
        const s = map.getSize();
        if (s.x !== el.clientWidth || s.y !== el.clientHeight) map.invalidateSize({ pan: false });
      });
    });
    ro.observe(el);
    return () => { cancelAnimationFrame(f); ro.disconnect(); };
  }, [map]);
  return null;
}
// inside <MapContainer>: <SizeWatcher />
```

**Add this to every Leaflet project where panels or layout can change the map's
size.**

### 5.3 Other settings worth copying

- `<MapContainer preferCanvas renderer={useMemo(() => L.canvas({ padding: 0.5 }), [])}>`
- `<TileLayer keepBuffer={6} … />`
- Moving layers on their own `<Pane>` with `pointer-events: none`.
- Per-frame DOM updates (rotation, tag text) written directly to elements, not
  through React state.
- Per-trajectory derived data cached in a `WeakMap` keyed on the trajectory
  object.

---

## 6. Procedure for another project

1. **Measure first.** Add an FPS meter (copy `FpsMeter.tsx`) and profile a
   realistic heavy case. Build a realistic test data set if none exists.
2. **Find out where the time goes.** Use Chrome DevTools → Performance, or the
   script in step 9. If "Scripting" dominates, it's React or JavaScript work
   (step 5). If "Rendering" or "Painting" dominates, it's the drawing (steps 3–4).
3. **Split canvases by how often things change.** Static layers go on one canvas
   or pane, moving layers on another. Keep moving panes `pointer-events: none`.
4. **Stop rebuilding per frame.** Create icons and markers once; update only
   position, rotation and text, directly on the DOM.
5. **Remove React re-renders per frame:**
   - Look for "Maximum update depth" warnings; they signal a loop.
   - Look for inline callbacks passed to `memo` components.
   - Skip work in closed panels.
   - Cache parsed timestamps and other derived data.
   - Use React DevTools "Highlight updates" to see what re-renders.
6. **If there are still thousands of moving objects, move them to the GPU.** Use
   deck.gl, pinned to an exact known-good version (9.3.11 worked; 9.4.0 crashed
   an RTX 4070).
   - Use the custom `DeckOverlay` (5.1), not `deck.gl-leaflet`.
   - Use `DataFilterExtension` for time windows.
   - Test mouse hits on the CPU instead of using GPU picking.
   - Keep a Leaflet fallback behind a `supportsWebGL2()` check.
7. **Fix panning and zooming:**
   - Sync the GPU overlay on every `move` (once per frame).
   - Put heavy static geometry on the GPU too.
   - Set `L.canvas({ padding: 0.5 })` and `keepBuffer={6}`.
   - Add `SizeWatcher` (5.2).
8. **Check that nothing broke:** hover, click, follow, tooltips, popups, z-order,
   colour modes, filters, hidden items, zoom animation, and the fallback path.
   Run the typecheck and the tests.
9. **Benchmark before and after under the same conditions.** Use Playwright with
   real Chromium:
   - Load the data, play, and drag the map (6 drags of 450 px over about 0.75 s
     each), then zoom in and out with the wheel.
   - Record the `requestAnimationFrame` frame times and `longtask` entries.
   - Report average fps, median, p95, p99, the worst frame, frames over 50 ms,
     and long tasks.
   - Take a screenshot mid-drag to catch missing areas.
   - Run once headed with the window visible, to use the real GPU and refresh
     rate.
   - Close other heavy apps, since background load skews results.
10. **Try it at the user's real screen size and scaling**, including after opening
    and closing panels. B9 only appeared after a layout change.

---

## 7. User feedback timeline

1. The user asked for an import-ready file of at least 500 realistic flights,
   built from the project's data. Delivered 520.
2. "Massive lag … so many lines … use more resources or a good server?" Answer:
   the lag was client-side drawing, so a server wouldn't help. Proposed steps 1–5.
3. Asked what happens with custom routes under step 1. Answer: they work. Static
   only means unchanged between frames; the canvas repaints once when routes
   change.
4. "Try 1–2." Result: 8.9 → 98 fps.
5. Asked (in Thai) what was traded for the smoothness. Answer:
   - Planes now sit above airport icons.
   - The hit area is nearly the same.
   - The follow ring now pulses.
   - One extra canvas, about 6 MB of memory (about 20 MB on HiDPI screens).
   - The live canvas can't be clicked.
   - A new code rule: moving layers need `pane={LIVE_PANE}`.
   - Plane rotation is done outside React.
   - No data or features were lost.
6. "Try number 5." GPU version built. After the comparison: 177 fps with 500
   flights and 116 fps with 1,990.
7. "Sound mute button and FPS counter like MSI Afterburner … panning stutters,
   parts half gone and loaded later." Delivered mute and the FPS meter, the
   `DeckOverlay` rewrite, GPU routes and dots, canvas padding and tile buffer.
8. "Still stutters … only see 60% of my screen from the left." Found B9, the
   stale Leaflet size after the panel closed, and fixed it with `SizeWatcher`.
   Measured about 170 fps at 180 Hz afterwards.

### Trade-offs of the final version

- Planes briefly scale with the map during the quarter-second zoom animation,
  like the static canvas does.
- Trail colours blend smoothly between points instead of changing in steps.
- Planes always draw above airport icons; before, the order depended on latitude.
- Route fix dots always draw above other routes' lines. With trail decay on, they
  sit under the trails, as before.
- The app bundle is bigger (four deck.gl packages), and the map uses two extra
  WebGL canvases.

---

## 8. Files touched

**New**

- `web/components/GpuTraffic.tsx`: deck.gl layers, tags, hit-testing, tooltips
  and popups.
- `web/components/DeckOverlay.ts`: the Leaflet ↔ deck.gl bridge.
- `web/components/FpsMeter.tsx`: the FPS overlay.
- `scripts/make_fts500_flights.py` and `web/lib/fts500Dummy.test.ts`.
- `dummy_data/fts_500_flights_20260927.csv`.

**Modified**

- `web/components/LeafletMap.tsx`: panes, GPU switch, `gpuRoutes` and
  `gpuTrails` memos, `SizeWatcher`, canvas renderer padding, `keepBuffer`, and
  the icon built once.
- `web/components/MapApp.tsx`: mute and FPS state, stable callback.
- `web/components/GeneratorPanel.tsx`: loop fix (memoisation).
- `web/components/FilterPanel.tsx`: skips work while closed.
- `web/lib/useSimPlayback.ts`: `WeakMap` duration cache.
- `web/lib/displayColors.ts`: `cssToRgba` and `Rgba`, with tests.
- `web/lib/mapPrefs.ts`: `loadFlag` and `saveFlag`.
- `web/components/cdr/ConflictLayer.tsx`: `pane` prop.
- `web/components/nav/MainNavigation.tsx` and `NavIcon.tsx`: mute and FPS buttons
  and icons.
- `web/app/globals.css`: pane pointer-events, GPU tag styles, `.fps-meter*`, and
  `.mnav-util-btn.active`.
- `web/package.json`: deck.gl packages at 9.3.11.

Final state: the typecheck is clean, and vitest passes 61 files and 938 tests.

---
---

# Part 2 — File import / "Generate all" speed-up (2026-09-30)

The user reported that even a small file "takes a long time". Measured with
`dummy_data/2000 flight.csv` (1,990 rows; 1,983 generate, the other 7 fail
the same way on the old code). All numbers were taken with a game using ~46 %
of the CPU in the background, before and after under the same conditions.

## 1. Results

| Step | Before | After |
|---|---|---|
| Reading the file into tabs | 0.4 s | 0.8 s |
| **Generate all** (1,990 flights) | **179.9 s** | **63.3 s** |
| Map settling afterwards | 7.5 s | 8.1 s (fixed in Part 3) |
| **Total** | **187.8 s** | **72.2 s** (2.6×) |
| Server alone, 400 flights, lighter load | 75 ms/flight | 20 ms/flight (3.7×) |

## 2. How it was measured

1. **End to end in the real browser** (`imp.mjs`, Playwright + Chromium):
   time from choosing the file to the "Imported N flights" note, from
   clicking Generate all to the route trigger appearing, and then until the
   main thread is calm (no frame over 50 ms for 1 s). Record every
   `/api/generate_batch` request's duration.
2. **Server profile in-process** (`prof_server.py`): save the real request
   bodies the browser sent (`batch_bodies.json`), replay them through
   `api.server.generate_batch` under `cProfile`. Sort by cumulative and by
   self time.
3. **Equivalence** (`equiv.py`): generate all 1,990 flights with the original
   code, the new code serial, and the new code parallel; SHA-256 the payload +
   the export GeoDataFrame (`to_csv()`) + the route feature per flight; all
   three digest sets must be identical.

## 3. Root cause

Reading the file was never the problem (0.4 s). The server built flights
**one at a time on one core** (`generate_batch` looped `_generate_one`),
65–90 ms each, and the browser sent chunks of 40 **one after another**, so
the round trip and JSON parse of each chunk left the server idle in between.
Inside a flight, 75 % of the time was `build_flight_timeline`'s along-track
integration (`_build_tables` → `_speed_at` → `profile.at` +
`target_tas_kt`), run up to three passes, and the altitude/TAS lookup in it
depends only on time — identical in every pass.

## 4. Fixes

1. **Parallel batch generation on the server** ([api/server.py](api/server.py),
   `_run_batch`): a batch of ≥ 4 flights fans out to a `ProcessPoolExecutor`
   (cores − 1, max 8; `ATC_GEN_WORKERS` overrides, `1` = old serial path).
   Threads would not help: pure-Python CPU work under the GIL, and
   `_GEN_LOCK` serialises it anyway. Each worker's `_generate_one` stashes its
   export bundle in the worker's own `_EXPORT_CACHE`; `_batch_one` pops it and
   ships it back with the payload, and the parent re-registers it with
   `_cache_export`, so `/api/download`, `/api/extend`, `/api/recache` and
   conflict marks work exactly as before. `pool.map` preserves order, so
   `flight_key` uniqueness and cache insertion order are unchanged. A
   `BrokenProcessPool` drops the pool and finishes that chunk inline.
2. **Memoise the unshaped (altitude, TAS) per integration midpoint** and
   **skip the refinement passes when there are no constraints**
   ([trajectory_sim/trajectory.py](trajectory_sim/trajectory.py)). `_shape`
   is a no-op without constraints, so pass 2 reproduced pass 1 exactly and
   the loop only stopped after paying for it. ~10 % per flight, identical
   output.
3. **Browser keeps 3 chunks in flight** ([GeneratorPanel.tsx](web/components/GeneratorPanel.tsx)
   `generateAll`, [api.ts](web/lib/api.ts)): the first chunk goes alone and
   the response now carries `workers`; only when the server reports more than
   one worker do the remaining chunks run 3 at a time (a serial host would
   just queue them and risk its timeout). Results still land in chunk order.
4. **Render pinned to serial** ([render.yaml](render.yaml)
   `ATC_GEN_WORKERS=1`): each worker holds its own navdata (~120 MB); the
   free plan has 512 MB.

## 5. Bugs hit along the way

| # | Problem | Cause | Fix |
|---|---|---|---|
| B13 | Killing the server left 7 worker processes alive (~840 MB) — every dev reload leaked a set | Nothing tells a `ProcessPoolExecutor`'s workers their parent died | `initializer=_gen_worker_init`: a daemon thread waits on `multiprocessing.parent_process().sentinel` and `os._exit(0)`s. Verified: all 7 gone within 0.5 s of a hard kill |
| B14 | `uvicorn --reload` on Windows sometimes keeps the OLD server process alive after a reload (port 8000 held by an orphan `spawn_main` child) | Pre-existing reloader behaviour, not the change | Kill the orphan; restart `npm run dev:serve` if a code change does not take effect |
| B15 | `json.dumps` of the payload failed in my test with "Timestamp is not JSON serializable" | The payload holds pandas Timestamps that FastAPI serialises but plain `json` cannot | Test only: `default=repr`; compare the gdf with `to_csv()` not `to_json()` |
| B16 | Python read a file with a `cp874` decode error | Thai Windows locale default encoding | Always `open(..., encoding="utf-8")` |
| B17 | A `Bash` `&`+`disown` background start reported "exited 0" while the servers kept running | Shell wrapper detached | Use the tool's own `run_in_background`; confirm with `Get-NetTCPConnection` |

## 6. Code review

`/code-review` was run on the unified diff (no git repo here). Reviewers
raised 4 candidates; each was independently scored 0/100: zero-flight
request (guarded by an earlier throw), non-HTTPException worker errors
failing the batch (pre-existing behaviour), whole-chunk recompute after a
worker crash (a chunk is 40 flights, ~3 s), and a race in `_EXPORT_CACHE`
eviction (already reachable before; `popitem` can never hit an empty dict
because eviction only runs above 4,000 entries).

## 7. Still open

- The remaining server cost is real arithmetic across all cores; the next
  lever would be vectorising `_build_tables` with numpy (a rewrite, output
  must stay identical).
- With the game closed the same run is expected well under a minute.

## 8. Files touched

`api/server.py`, `trajectory_sim/trajectory.py`,
`web/components/GeneratorPanel.tsx`, `web/lib/api.ts`, `render.yaml`.
Typecheck clean; vitest 930 passed / 8 skipped (the skips are
`ftsStarDummy.test.ts`, whose fixture `fts_traffic_20260709Star.csv` was
renamed to `2000 flight.csv`).

---
---

# Part 3 — Smooth pan/zoom and no freeze after import (2026-10-01)

User report: "fps drops when panning in/out and left/right but is perfect
when still", and "once the file is imported the web froze for a bit".
Measured with the 1,990-flight day loaded. Test machine as before (180 Hz
screen, RTX 4070), other apps running.

## 1. Results

| Scenario | Before | After |
|---|---|---|
| Pan + zoom, default zoom (6 drags, 4 wheel zooms) | 121 fps avg · p95 16.8 ms · p99 33 ms · worst 78 ms · 9 frames > 50 ms · 5 long tasks | **179 fps · p95 5.6 ms · p99 5.7 ms · worst 17 ms · 0 · 0** |
| Same, zoomed in 3 steps | 158 fps · p95 5.8 ms · worst 83 ms · 8 frames > 50 ms | **178 fps · p95 5.6 ms · worst 11 ms · 0** |
| Main-thread busy time during the pan test | 7.6 s | 1.9 s |
| Main-thread tasks > 30 ms during the 4 zooms | 16–18 | **0** |
| Settle after "Generate all" (results shown → main thread calm) | 10.4 s, with three ~4 s freezes | ~1.4 s of stalls, the longest 0.63 s |
| Conflict scan (1,983 flights → 1,319 conflicts) | ~4 s on the main thread, run 3× | 3.3 s in a worker, run once (a superseded first run is killed) |
| First deck.gl frame after import | 724 ms | 131 ms |

179 fps is the screen's refresh rate: the pan is now vsync-bound.

## 2. How it was measured (the procedure that found each cause)

1. **Frame times without a profiler first.** `pan.mjs` (Playwright): load the
   set, 6 drags of 450 px at ~60 mouse events/s, 4 wheel zooms; record
   `requestAnimationFrame` gaps and `PerformanceObserver` long tasks; report
   avg fps, p95, p99, worst, frames > 50 ms. The CPU profiler itself adds
   stalls (a "717 ms worst frame" that vanished without it), so profiled
   runs are for attribution only, never for the numbers.
2. **Chrome trace, aggregated.** Sampling profiles put 5.5 s in
   "(program)" — native work they cannot name. `Tracing.start` with
   `devtools.timeline` + `toplevel`, then sum self-time per event name per
   thread: it showed `Layerize`, `Commit`, `UpdateLayer`, `HitTest` on the
   main thread and a 40 %-busy compositor — a layer-count problem.
3. **Break each long task into its children** (`trace2.mjs`): for every
   top-level main-thread task > 30 ms, sum child events and list the
   `FunctionCall`/`EventDispatch`/`TimerFire` entry points. This put every
   stall inside Leaflet's zoom animation (`_animateZoom` → 30 ms of style
   recalc → `transitionend` → a React render).
4. **Count the DOM under the map** (`querySelectorAll` per pane, and
   elements with `translate3d`). 4,320 nodes, 2,037 markers, 2,043
   `leaflet-zoom-animated` elements: one marker per flight in GPU mode.
5. **For the freeze:** a sampling profile from the moment the last chunk
   arrives (`settle_prof.mjs`) attributed to the nearest owner frame
   (deck.gl / react-dom / app file), plus the child-breakdown trace with a
   `performance.mark` at "results shown". Together they separated shader
   compiles, a GPU readback, timestamp parsing, React commits and GC.
6. **Verify with the same scripts after each change**, one change at a time
   where the attribution was uncertain (`keepBuffer` 6 → 2 was measured on
   its own: a small gain, not the cause).

## 3. Root causes

**Pan/zoom**

1. **One Leaflet `Marker` per flight survived the GPU move**: the "R1, R2…"
   route-index pill at each route's start (`routeIndexBadge`) was not gated
   on GPU mode. Each marker is a `translate3d` element → its own composited
   layer. 2,000 layers made every drag frame pay compositor time
   (`Layerize`/`Commit`), every mouse move a 3 ms hit-test, and every zoom
   animate 2,000 transitions (30 ms style recalc + 15–42 ms `_animateZoom`
   per frame, then a React render inside `transitionend`).
2. `keepBuffer={6}` (Part 1) kept ~300 tiles per tile layer alive — 3.5×
   the default — each a composited layer. Minor next to (1), but real.

**Freeze after import**

3. `scanFlightPlanConflicts` (all ~2 million pairs) ran synchronously in a
   `useMemo` — ~4 s — and ran again on every airspace-sector load, because
   `sepMinNmAt` was keyed on the whole `airspaceIndex`, which is rebuilt per
   load. Three freezes of 4 s.
4. The first deck.gl frame compiled every layer's shaders (341 ms blocking
   in `getProgramParameter`) and the plane-icon `toDataURL` stalled 175 ms on
   a GPU readback because the canvas was GPU-backed and the GPU was busy.
5. A million `Date.parse`/`new Date` calls on `epoch_ts`: `toSamples`
   (224 ms) and the PDR `pathFromTrajectory` (323 ms), inside the React
   render that shows the results.
6. The PDR incursion check scanned in fixed chunks of 100 flights (60–140 ms
   each) and published partial results 5×, each publish re-rendering the
   1,990 mounted route cards (~60 ms).
7. Every MapApp render re-rendered all 1,990 route cards (inline `onRemove`
   closures defeated memoisation).

## 4. Fixes

| # | Change | File |
|---|---|---|
| 1 | Route-index pills drawn by a deck.gl `TextLayer` (background = route colour, same offsets as the divIcon); the `Marker` kept only for `!gpu` | `GpuTraffic.tsx` (`GpuRouteBadge`), `LeafletMap.tsx` (`gpuRoutes.badges`) |
| 2 | `keepBuffer` back to Leaflet's default 2 | `LeafletMap.tsx` |
| 3 | Full plan scan in a **Web Worker**: `usePlanScan` posts the flights as flat `Float64Array`s (`planScanPacked.ts`), the worker rebuilds them and runs the unchanged `scanFlightPlanConflicts`; a request that supersedes a running one terminates the worker; incremental rescans (one flight moved) stay synchronous; `pending` makes the auto-resolve pass wait and the download stamp fall back to an inline scan | `lib/cdr/usePlanScan.ts`, `planScan.worker.ts`, `planScanPacked.ts`, `sepMinAt.ts`, `MapApp.tsx` |
| 3b | `sepMinNmAt` keyed on the loaded TMA **collection** (`sectorData.tma`), and the worker builds the same resolver from it (`makeSepMinNmAt`), so the config — and the scan — changes once, not per sector file | `MapApp.tsx`, `sepMinAt.ts` |
| 4 | Every deck layer type stays mounted with `data: []` when idle, so shaders compile at page load; icons rasterised at the first ready render on a `willReadFrequently` (CPU) canvas | `GpuTraffic.tsx` |
| 5 | The API emits `t_s` (elapsed seconds) per point; `toSamples`, `totalSeconds` and `pathFromTrajectory` use it and fall back to the stamp for imported/re-timed points | `api/server.py`, `lib/trajectory/types.ts`, `useSimPlayback.ts`, `usePdrCheck.ts` |
| 6 | PDR scan steps are time-budgeted (6 ms) instead of 100 flights; publishes at the first step, every 1,000 flights and the end | `lib/pdr/usePdrCheck.ts` |
| 7 | `RouteResultTabs` wrapped in `memo` (with `airspace` compared by value); per-flight `onRemove`/`onToggleCollapse` handlers cached and reading the latest callbacks through a ref; `content-visibility: auto` on `.rt-card` | `RouteResultTabs.tsx`, `MapApp.tsx`, `globals.css` |

Equivalence: `planScanPacked.test.ts` proves the packed round trip is exact
and that scanning the unpacked flights yields the identical conflict list;
the worker's config is the main thread's config minus its function, plus the
same resolver rebuilt from the same polygon.

## 5. Bugs and gotchas hit

| # | Problem | Cause | Fix / lesson |
|---|---|---|---|
| B18 | Profiled pan runs showed 700 ms frames that did not exist | CPU profiler overhead at a 200 µs sampling interval | Take numbers without the profiler; profile only to attribute |
| B19 | Sampling profile put most time in "(program)" | Native compositor/style work is invisible to the JS profiler | Use a Chrome trace aggregated by event name; break long tasks into children |
| B20 | Worker result never appeared in tests | The test closed the browser 3 s after the results; the scan takes 3.3 s | Wait; log `[plan-scan]` lines |
| B21 | Worker was superseded twice per import | `buildAirspaceIndex` rebuilds every entry array on each sector load | Key on the raw collection, not the index |
| B22 | `getProgramParameter` 341 ms in the import frame | Shader compile on first use of each layer type | Mount all layer types empty at load |
| B23 | `toDataURL` 175 ms for a 64 px canvas | GPU readback waiting on a busy GPU queue | `getContext("2d", { willReadFrequently: true })`, rasterise early |
| B24 | uvicorn `--reload` did not pick up `server.py` (no `t_s` in responses) | B14 again | Restart `npm run dev:serve`; verify with a real request |
| B25 | `content-visibility: auto` needs a placeholder height | Otherwise the scroll height collapses | `contain-intrinsic-size: auto 56px` |

## 6. Still open

- The 0.63 s React commit when 1,990 result cards mount. The fix is a
  virtualised card list (render only the cards in view); `content-visibility`
  already removes their layout/paint. A production build roughly halves the
  commit as well (dev-mode validation is a third of it).
- GC pauses of 30–110 ms during the first seconds after import: the heap
  holds ~2 million point/sample objects. Typed-array sample tables would cut
  this; not done.
- The 0.19 s render when the scan's 1,319 conflicts land (dashboard + menu).
- Profile pins (TOC/TOD) are still Leaflet markers per flight when enabled.

## 7. Procedure to reuse

1. Numbers without a profiler; attribution with one.
2. When JS looks cheap but frames are slow, trace the compositor: count
   composited layers (`translate3d` elements, tiles kept, markers). Every
   per-item DOM element with a 3D transform is a layer.
3. Break long tasks into children with entry-point attribution; a stall
   that only happens on zoom/drag end has a handler with a name.
4. Anything O(n²) over the data set goes to a worker; pass typed arrays,
   supersede by terminating, keep incremental updates on the main thread.
5. Warm GPU shaders and rasterised assets at page load, never in the frame
   that builds a large scene.
6. Send numbers, not strings, for per-point time.
7. Time-budget background scans and publish partial results sparingly; memoise
   list rows with stable handlers; `content-visibility: auto` on long lists.

## 8. Files touched

New: `web/lib/cdr/usePlanScan.ts`, `planScan.worker.ts`,
`planScanPacked.ts`, `planScanPacked.test.ts`, `sepMinAt.ts`.
Modified: `web/components/MapApp.tsx`, `LeafletMap.tsx`, `GpuTraffic.tsx`,
`RouteResultTabs.tsx`, `web/lib/useSimPlayback.ts`, `lib/pdr/usePdrCheck.ts`,
`lib/trajectory/types.ts`, `web/app/globals.css`, `api/server.py`.
Typecheck clean; vitest 934 passed / 8 skipped; pytest 15 passed.
