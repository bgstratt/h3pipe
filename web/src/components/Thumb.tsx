import { useCallback, useState, type CSSProperties, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { openMenu, openViewer } from "../actions";
import { api } from "../host";
import type { Badge } from "../lib/format";
import { tn } from "../lib/format";
import { stripCell, stripStyle } from "../lib/strip";
import type { Pass, TakeSummary } from "../types";

/** Hover scrub: the strip cell under the pointer, or null when not hovering. */
export function useScrub(): [number | null, { onPointerMove: (e: PointerEvent<HTMLElement>) => void; onPointerLeave: () => void }] {
  const [cell, setCell] = useState<number | null>(null);
  const onPointerMove = useCallback((e: PointerEvent<HTMLElement>) => {
    if (e.pointerType === "touch") return;
    const r = e.currentTarget.getBoundingClientRect();
    setCell(stripCell(e.clientX - r.left, r.width));
  }, []);
  const onPointerLeave = useCallback(() => setCell(null), []);
  return [cell, { onPointerMove, onPointerLeave }];
}

/** Background style for a take's picture: the strip cell while scrubbing, else the thumb. */
export function mediaStyle(ep: string, take: Pick<TakeSummary, "thumb" | "strip"> | null | undefined, cell: number | null): CSSProperties {
  if (!take) return {};
  if (cell != null && take.strip) return stripStyle(api().fileUrl(ep, take.strip), cell);
  if (take.thumb) return { backgroundImage: `url("${api().fileUrl(ep, take.thumb)}")` };
  return {};
}

export function statusClass(status: string, rendering = false): string {
  return rendering ? "h3-s-rendering" : `h3-s-${status}`;
}

interface ThumbProps {
  ep: string;
  pass: Pass;
  shot: string;
  take: TakeSummary | null | undefined;
  height: number;
  aspect: number;
  selected?: boolean;
  label?: string | null;
  className?: string;
  onClick?: (e: MouseEvent) => void;
  onDoubleClick?: (e: MouseEvent) => void;
  children?: ReactNode;
}

/** A take's thumbnail with hover scrub and the shared context menu. */
export function Thumb(p: ThumbProps) {
  const [cell, scrub] = useScrub();
  const t = p.take;
  const has = !!(t && (t.thumb || t.strip));
  const style: CSSProperties = { height: p.height, width: Math.round(p.height * p.aspect), ...mediaStyle(p.ep, t, cell) };
  const onContextMenu = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    openMenu(e.clientX, e.clientY, p.shot, t?.take ?? null, p.pass);
  };
  const onDoubleClick = p.onDoubleClick ?? ((e: MouseEvent) => {
    e.stopPropagation();
    if (t) openViewer(p.shot, t.take, null, undefined, p.pass);
  });
  const title = t ? `${p.shot} ${tn(t.take)} · ${t.status}${t.strip ? " · hover to scrub" : ""} · double-click to play · right-click for more` : p.shot;
  return (
    <div
      className={`h3-thumb${has ? "" : " h3-empty"}${p.selected ? " h3-sel" : ""}${p.className ? " " + p.className : ""}`}
      style={style}
      title={title}
      onClick={p.onClick}
      onDoubleClick={onDoubleClick}
      onContextMenu={onContextMenu}
      {...scrub}
    >
      {!has && <span>{t ? (t.status === "queued" ? "queued" : t.status === "failed" ? "failed" : "no thumb") : "—"}</span>}
      {p.label && <span className="h3-thumb-label">{p.label}</span>}
      {p.children}
    </div>
  );
}

export function Badges({ badges, max }: { badges: Badge[]; max?: number }) {
  const shown = max != null ? badges.slice(0, max) : badges;
  const rest = max != null ? badges.length - shown.length : 0;
  if (!badges.length) return null;
  return (
    <span className="h3-badges">
      {shown.map((b) => (
        <span key={b.kind} className={`h3-badge h3-b-${b.kind}`} title={b.title}>
          {b.label}
        </span>
      ))}
      {rest > 0 && <span className="h3-badge" title={badges.slice(shown.length).map((b) => b.label).join(", ")}>+{rest}</span>}
    </span>
  );
}

export function Progress({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div className="h3-progress" title={`${value}/${max}`}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}
