// Reuse the preserved prototype's actual implementation, not a parallel port.
import "../../phase1_viewer_frontend/geometry.js";

export interface View {scale: number; offsetX: number; offsetY: number}
export interface Point {x: number; y: number}
interface Geometry {
  fit(w: number, h: number, vw: number, vh: number, padding?: number): View;
  toNative(view: View, x: number, y: number): Point;
  toScreen(view: View, x: number, y: number): Point;
  zoomAt(view: View, factor: number, x: number, y: number, min: number, max: number): View;
  panBy(view: View, dx: number, dy: number): View;
  resize(view: View, oldW: number, oldH: number, w: number, h: number): View;
}
declare global {var FloorplanViewport: Geometry}
export const geometry: Geometry = globalThis.FloorplanViewport;
