// Target UI pieces (Phase 7/8): the picker, the hooks that load the target list
// and the widget choices a target's binding allows, and the target badge.

import { useEffect, useMemo } from "react";
import { loadModelFiles, loadTargets, loadWidgetChoices } from "../actions";
import {
  findTarget, listDefaultTarget, pickerChoices, pickerSpec, seriesDefaultTarget, videoTargets, widgetKey, type WidgetSpec,
} from "../lib/targets";
import { targetOptionText } from "../lib/readiness";
import { useApp } from "../store";
import type { ModelList, Target, TargetList } from "../types";
import { useStatus } from "./hooks";

export interface TargetsInfo {
  /** null until loaded, or on a server without /h3pipe/targets */
  list: TargetList | null;
  video: Target[];
  /** the series default video target (the episode's own, else the list's) */
  seriesDefault: string;
  error: string | null;
}

/** The target list (loaded on first use) and the series default. */
export function useTargets(): TargetsInfo {
  const list = useApp((s) => s.targets);
  const error = useApp((s) => s.targetsError);
  const st = useStatus();
  useEffect(() => {
    void loadTargets();
  }, []);
  const video = useMemo(() => videoTargets(list), [list]);
  return { list, video, seriesDefault: seriesDefaultTarget(list, st), error };
}

function useChoices(spec: WidgetSpec): string[] | null | undefined {
  const key = spec.kind === "node" ? widgetKey(spec) : null;
  const choices = useApp((s) => (key ? s.widgetChoices[key] : undefined));
  useEffect(() => {
    if (spec.kind === "node") void loadWidgetChoices(spec.class_type, spec.field);
  }, [spec]);
  return pickerChoices(spec, key && choices ? { [key]: choices } : undefined);
}

export interface TargetPickers {
  /** undefined = ComfyUI's generic list; null = the target has no such widget (hide it) */
  models: string[] | null | undefined;
  loras: string[] | null | undefined;
  target: Target | undefined;
  /** GET /h3pipe/models for the model param: the files grouped by the target's
   * family (undefined: not loaded, or a server without the route) */
  modelFiles: ModelList | undefined;
  /** the target has a low-noise model (`models.model_low`: Wan 2.2 14B) */
  twoStage: boolean;
  /** GET /h3pipe/models for `model_low` */
  modelLowFiles: ModelList | undefined;
}

/** What the model and LoRA pickers may offer under `targetId`. */
export function useTargetPickers(targetId: string | null | undefined): TargetPickers {
  const list = useApp((s) => s.targets);
  const target = findTarget(list, targetId);
  const spec = useMemo(() => pickerSpec(target), [target]);
  const models = useChoices(spec.model);
  const loras = useChoices(spec.loras);
  const hasFamily = !!target?.models?.model && spec.model.kind === "node";
  const modelFiles = useApp((s) => (hasFamily && target ? s.modelFiles[`${target.id}|model`] : undefined));
  useEffect(() => {
    if (hasFamily && target) void loadModelFiles(target.id, "model");
  }, [hasFamily, target]);
  // Phase 8.5: a two-stage target's low-noise model (GET /h3pipe/models?param=model_low)
  const twoStage = !!target?.models?.model_low;
  const modelLowFiles = useApp((s) => (twoStage && target ? s.modelFiles[`${target.id}|model_low`] : undefined));
  useEffect(() => {
    if (twoStage && target) void loadModelFiles(target.id, "model_low");
  }, [twoStage, target]);
  return { models, loras, target, modelFiles, twoStage, modelLowFiles };
}

/** A select of the video targets, the default marked. */
export function TargetSelect({ value, onChange, list, video, disabled, title, extra }: {
  value: string;
  onChange: (id: string) => void;
  list: TargetList | null;
  video: Target[];
  disabled?: boolean;
  title?: string;
  /** an extra first option (e.g. "each shot's own target"), value "" */
  extra?: string;
}) {
  const def = listDefaultTarget(list);
  const known = !value || video.some((t) => t.id === value);
  return (
    <select className="h3-in" value={value} disabled={disabled} title={title} onChange={(e) => onChange(e.target.value)}>
      {extra != null && <option value="">{extra}</option>}
      {!known && <option value={value}>{value} (unknown target)</option>}
      {video.map((t) => (
        <option key={t.id} value={t.id} title={t.id}>
          {targetOptionText(t, t.id === def)}
        </option>
      ))}
    </select>
  );
}
