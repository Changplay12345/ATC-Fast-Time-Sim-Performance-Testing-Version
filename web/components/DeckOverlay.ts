/**
 * DeckOverlay — a deck.gl canvas living in a Leaflet pane, kept in lockstep
 * with the map.
 *
 * Replaces the deck.gl-leaflet package, which re-synced the deck view only when
 * a pan or zoom ENDED. Its canvas was exactly one screen big and simply rode
 * along with the map pane during a drag, so everything dragged in from beyond
 * the original screen edge was missing until the mouse was released — traffic
 * and trails visibly cut off along a straight line, then popping in.
 *
 * Here the canvas is re-centred on the viewport and redrawn on EVERY map
 * `move` (coalesced to one sync per animation frame), so a drag always shows
 * the full picture. The DOM move and the redraw happen in the same frame, so
 * nothing jitters. Zoom animation is a CSS transform of the last frame, like
 * Leaflet's own vector renderer, then a full sync at the end.
 */

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
    // L.Layer has no initialize(), so constructor options would be dropped.
    L.setOptions(this, { pane });
    this.deckProps = props;
  }

  onAdd(map: L.Map): this {
    const pane = this.getPane();
    if (!pane) return this;
    const el = L.DomUtil.create("div", "leaflet-layer gpu-deck");
    if (map.options.zoomAnimation && L.Browser.any3d) {
      L.DomUtil.addClass(el, "leaflet-zoom-animated");
    }
    pane.appendChild(el);
    this.container = el;
    this.deck = new Deck({
      ...this.deckProps,
      parent: el,
      controller: false,
      style: { zIndex: "auto" },
      viewState: viewState(map),
    });
    this.sync();
    return this;
  }

  onRemove(): this {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.deck?.finalize();
    this.deck = null;
    this.container?.remove();
    this.container = null;
    return this;
  }

  getEvents() {
    return {
      move: this.scheduleSync,
      zoomend: this.sync,
      viewreset: this.sync,
      resize: this.sync,
      zoomanim: this.animZoom as L.LeafletEventHandlerFn,
    };
  }

  setProps(props: DeckProps): void {
    Object.assign(this.deckProps, props);
    this.deck?.setProps(props);
  }

  /** `move` fires per mouse event during a drag — often faster than the screen
   *  refreshes — so the sync is coalesced to one per frame. Until it runs the
   *  canvas rides along with the map pane, so it is never misaligned. */
  private scheduleSync(): void {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => {
      this.raf = 0;
      this.sync();
    });
  }

  private sync(): void {
    const map = this._map as LeafletMapInternals | undefined;
    const el = this.container;
    if (!map || !el || !this.deck) return;
    if (map._animatingZoom) return; // the CSS transform owns the canvas meanwhile
    const size = map.getSize();
    el.style.width = `${size.x}px`;
    el.style.height = `${size.y}px`;
    // Put the canvas back over the viewport (it is inside the moving pane).
    L.DomUtil.setPosition(el, map._getMapPanePos().multiplyBy(-1));
    this.deck.setProps({ viewState: viewState(map) });
    this.deck.redraw("leaflet-sync");
  }

  /** Scale/translate the current frame to where the zoom animation is going —
   *  the same transform Leaflet's own vector Renderer applies. */
  private animZoom(e: L.ZoomAnimEvent): void {
    const map = this._map;
    const el = this.container;
    if (!map || !el) return;
    const scale = map.getZoomScale(e.zoom, map.getZoom());
    const position = L.DomUtil.getPosition(el);
    const viewHalf = map.getSize().multiplyBy(0.5);
    const currentCenterPoint = map.project(map.getCenter(), e.zoom);
    const destCenterPoint = map.project(e.center, e.zoom);
    const centerOffset = destCenterPoint.subtract(currentCenterPoint);
    const topLeftOffset = viewHalf
      .multiplyBy(-scale)
      .add(position)
      .add(viewHalf)
      .subtract(centerOffset);
    if (L.Browser.any3d) L.DomUtil.setTransform(el, topLeftOffset, scale);
    else L.DomUtil.setPosition(el, topLeftOffset);
  }
}
