// P5: a camera aimed into a 360 panorama (equirectangular, 2:1). The view is
// cut out in the browser, pixel by pixel: for each output pixel, the ray through
// it (pinhole camera, horizontal field of view `fov`), turned by `pitch` about
// the camera's X axis then `yaw` about the world's Y, read off the panorama at
// that longitude and latitude with bilinear filtering (wrapping round in
// longitude). Pure functions on RGBA byte arrays, so the viewer can draw a
// small preview while dragging and the full-size plate when saving.

export interface PanoAim {
  /** degrees; 0 = the panorama's centre, positive = turn right */
  yaw: number;
  /** degrees; positive = look up */
  pitch: number;
  /** the horizontal field of view, degrees */
  fov: number;
}

export const FOV_MIN = 20;
export const FOV_MAX = 120;
export const PITCH_MAX = 85;

export interface Rgba {
  width: number;
  height: number;
  data: Uint8ClampedArray | Uint8Array;
}

/** An aim made legal: yaw wrapped to -180..180, pitch and fov clamped. */
export function clampAim(a: PanoAim): PanoAim {
  const yaw = ((((a.yaw + 180) % 360) + 360) % 360) - 180;
  return {
    yaw,
    pitch: Math.max(-PITCH_MAX, Math.min(PITCH_MAX, a.pitch)),
    fov: Math.max(FOV_MIN, Math.min(FOV_MAX, a.fov)),
  };
}

/** Where on the panorama (pixels, fractional) the centre of output pixel (x, y) looks. */
export function sourcePoint(aim: PanoAim, w: number, h: number, x: number, y: number, pw: number, ph: number): [number, number] {
  const f = w / 2 / Math.tan((aim.fov * Math.PI) / 360);
  let dx = x + 0.5 - w / 2;
  let dy = -(y + 0.5 - h / 2);
  let dz = f;
  const n = Math.hypot(dx, dy, dz);
  dx /= n; dy /= n; dz /= n;
  const p = (aim.pitch * Math.PI) / 180;
  const cy = dy * Math.cos(p) + dz * Math.sin(p);     // pitch about X
  const cz = -dy * Math.sin(p) + dz * Math.cos(p);
  const yw = (aim.yaw * Math.PI) / 180;
  const wx = dx * Math.cos(yw) + cz * Math.sin(yw);  // yaw about Y
  const wz = -dx * Math.sin(yw) + cz * Math.cos(yw);
  const lon = Math.atan2(wx, wz);
  const lat = Math.asin(Math.max(-1, Math.min(1, cy)));
  return [(lon / (2 * Math.PI) + 0.5) * pw - 0.5, (0.5 - lat / Math.PI) * ph - 0.5];
}

/** Draw the view `aim` of panorama `src` into `out` (its own width and height). */
export function renderView(src: Rgba, out: Rgba, aim: PanoAim): void {
  const { width: pw, height: ph, data: s } = src;
  const { width: w, height: h, data: o } = out;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const [u, v] = sourcePoint(aim, w, h, x, y, pw, ph);
      const u0f = Math.floor(u);
      const v0f = Math.floor(v);
      const fu = u - u0f;
      const fv = v - v0f;
      const u0 = ((u0f % pw) + pw) % pw;
      const u1 = (u0 + 1) % pw;
      const v0 = Math.max(0, Math.min(ph - 1, v0f));
      const v1 = Math.max(0, Math.min(ph - 1, v0f + 1));
      const a = (v0 * pw + u0) * 4, b = (v0 * pw + u1) * 4, c = (v1 * pw + u0) * 4, d = (v1 * pw + u1) * 4;
      const i = (y * w + x) * 4;
      for (let k = 0; k < 3; k++) {
        o[i + k] = (s[a + k] * (1 - fu) + s[b + k] * fu) * (1 - fv) + (s[c + k] * (1 - fu) + s[d + k] * fu) * fv;
      }
      o[i + 3] = 255;
    }
  }
}

/** How sharp the cut will be: source pixels per output pixel across the view (1 = full detail). */
export function viewDetail(aim: PanoAim, outWidth: number, panoWidth: number): number {
  return (panoWidth * (aim.fov / 360)) / outWidth;
}
