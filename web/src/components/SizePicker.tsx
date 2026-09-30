// The render size picker: the shot's own size, the offered sizes that fit the
// target's grid, or any size typed in (Custom…), checked against the grid as you
// type. Shared by the New take dialog (this take only) and the inspector's override.

import { useState } from "react";
import { sizeCheck, sizeChoices, type SizeRule } from "../lib/size";

const CUSTOM = "__custom__";

export function SizePicker(p: {
  /** "" = the shot's own size; else "WxH" (or text being typed) */
  value: string;
  onChange: (v: string) => void;
  /** the shot's own size, "WxH" ("" when unknown) */
  own: string;
  /** what the empty choice is called: "the shot's", "built" */
  ownLabel: string;
  rule: SizeRule;
  width?: number;
}) {
  const choices = sizeChoices(p.rule).filter((s) => s !== p.own);
  const [custom, setCustom] = useState(() => !!p.value && !choices.includes(p.value));
  const check = sizeCheck(p.value, p.rule);
  const pick = (v: string) => {
    if (v === CUSTOM) {
      setCustom(true);
      p.onChange(p.value || p.own);
    } else {
      setCustom(false);
      p.onChange(v);
    }
  };
  return (
    <div className="h3-col" style={{ gap: 2 }}>
      <div className="h3-row h3-wrap">
        <select className="h3-in" style={{ width: p.width ?? 200 }} value={custom ? CUSTOM : p.value} onChange={(e) => pick(e.target.value)}>
          <option value="">{p.own ? `${p.own} (${p.ownLabel})` : `(${p.ownLabel})`}</option>
          {choices.map((s) => <option key={s} value={s}>{s}</option>)}
          <option value={CUSTOM}>Custom…</option>
        </select>
        {custom && (
          <input
            className="h3-in"
            style={{ width: 120 }}
            placeholder="1344x768"
            value={p.value}
            onChange={(e) => p.onChange(e.target.value.replace(/[^\dxX×]/g, ""))}
            title={p.rule.multiple > 1 ? `Width x height, each a multiple of ${p.rule.multiple}` : "Width x height"}
          />
        )}
      </div>
      {check && <span className={`h3-small ${check.level === "err" ? "h3-err" : "h3-muted"}`}>{check.text}</span>}
    </div>
  );
}
