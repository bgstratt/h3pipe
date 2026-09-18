import { describe, expect, it } from "vitest";
import { ApiError, createHttpApi, isSeed, parseJsonSeedSafe, parseSeed, type Transport } from "../src/api";
import type { RenderRequest } from "../src/types";

const BIG = "6430499148929255544"; // > 2^53: a JS number would round it

describe("parseJsonSeedSafe", () => {
  it("keeps a 63-bit numeric seed exact, as a string", () => {
    const o = parseJsonSeedSafe<{ seed: string }>(`{"seed": ${BIG}}`);
    expect(o.seed).toBe(BIG);
    // what plain JSON.parse would have done
    expect(String(JSON.parse(`{"seed": ${BIG}}`).seed)).not.toBe(BIG);
  });

  it("handles nested seeds, arrays, line ends and negative numbers", () => {
    const text = `{"shots":[{"id":"sh020","seed":${BIG}}],"sidecar":{"seed":
      12345
    ,"steps":4},"x":[{"seed":-7}]}`;
    const o = parseJsonSeedSafe<{ shots: { seed: string }[]; sidecar: { seed: string; steps: number }; x: { seed: string }[] }>(text);
    expect(o.shots[0].seed).toBe(BIG);
    expect(o.sidecar.seed).toBe("12345");
    expect(o.sidecar.steps).toBe(4);
    expect(o.x[0].seed).toBe("-7");
  });

  it("leaves string seeds, nulls and other numeric keys alone", () => {
    const o = parseJsonSeedSafe<Record<string, unknown>>(`{"seed":"${BIG}","seed_source":"new","steps":8,"frames":107,"other_seed":5}`);
    expect(o).toEqual({ seed: BIG, seed_source: "new", steps: 8, frames: 107, other_seed: 5 });
    expect(parseJsonSeedSafe<{ seed: null }>(`{"seed": null}`).seed).toBeNull();
  });

  it("doesn't touch a 'seed' that only appears inside a string value", () => {
    const prompt = `use \\"seed\\": 123 here`;
    const o = parseJsonSeedSafe<{ prompt: string; seed: string }>(`{"prompt":"${prompt}","seed":1}`);
    expect(o.prompt).toBe(`use "seed": 123 here`);
    expect(o.seed).toBe("1");
  });
});

describe("parseSeed / isSeed", () => {
  it("accepts digits and returns a string, never a number", () => {
    expect(parseSeed(` ${BIG} `)).toBe(BIG);
    expect(parseSeed("0")).toBe("0");
    expect(parseSeed("007")).toBe("7");
    expect(typeof parseSeed("42")).toBe("string");
  });
  it("rejects anything else with a readable message", () => {
    expect(() => parseSeed("12a")).toThrow(/whole number/);
    expect(() => parseSeed("")).toThrow(/whole number/);
    expect(() => parseSeed("1.5")).toThrow();
    expect(() => parseSeed("-3")).toThrow();
    expect(() => parseSeed("99999999999999999999")).toThrow(/64 bits/);
  });
  it("isSeed only takes digit strings", () => {
    expect(isSeed(BIG)).toBe(true);
    expect(isSeed(123)).toBe(false);
    expect(isSeed("1e5")).toBe(false);
  });
});

// a fake ComfyUI: records requests, answers from a table
function fake(routes: Record<string, (init?: RequestInit) => Response | Promise<Response>>) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const t: Transport = {
    async fetch(path, init) {
      calls.push({ path, init });
      const key = `${init?.method ?? "GET"} ${path.split("?")[0]}`;
      const h = routes[key];
      if (!h) return new Response("404: Not Found", { status: 404, statusText: "Not Found" });
      return h(init);
    },
    url: (p) => `http://comfy${p}`,
  };
  return { api: createHttpApi(t), calls };
}

const json = (body: string, status = 200) => new Response(body, { status, headers: { "Content-Type": "application/json" } });

describe("createHttpApi", () => {
  it("returns seeds as exact strings even if the server sent numbers", async () => {
    const { api } = fake({
      "GET /h3pipe/episode": () => json(`{"episode":"ep05","pass":"proxy","shots":[{"shot":"sh020","takes":[{"take":1,"seed":${BIG}}]}]}`),
    });
    const st = await api.episode("C:\\ep05", "proxy");
    expect(st.shots[0].takes[0].seed).toBe(BIG);
  });

  it("sends a typed seed as the same string", async () => {
    let body = "";
    const { api } = fake({
      "POST /h3pipe/render": (init) => {
        body = String(init?.body);
        return json(`{"queued":[{"shot":"sh020","take":3,"prompt_id":"p1","seed":"${BIG}","seed_source":"typed"}],"skipped":[],"errors":[]}`);
      },
    });
    const req: RenderRequest = {
      ep: "C:\\ep05", pass: "proxy", shots: ["sh020"], redo: true, seed_mode: "auto", seed: BIG,
      model: null, loras: null, steps: null, prompt: null, parent_take: 2, note: "",
    };
    const r = await api.render(req);
    expect(body).toContain(`"seed":"${BIG}"`);
    expect(JSON.parse(body).seed).toBe(BIG);
    expect(r.queued[0].seed).toBe(BIG);
  });

  it("refuses a seed that isn't a digit string, before any request", async () => {
    const { api, calls } = fake({});
    const bad = { seed: 123 as unknown as string } as RenderRequest;
    await expect(api.render({ ...bad, ep: "x", pass: "proxy", shots: [] })).rejects.toThrow(/string of digits/);
    await expect(api.putOverride({ ep: "x", pass: "proxy", shot: "s", both: false, fields: { seed: 5 as unknown as string } })).rejects.toThrow(/string of digits/);
    expect(calls).toHaveLength(0);
  });

  it("puts ep/pass/shot in the query string, encoded", async () => {
    const { api, calls } = fake({ "GET /h3pipe/shot": () => json(`{"shot":"sh020"}`) });
    await api.shot("C:\\Shows\\My Show\\ep05", "final", "sh020");
    expect(calls[0].path).toBe("/h3pipe/shot?ep=C%3A%5CShows%5CMy+Show%5Cep05&pass=final&shot=sh020");
    expect(api.fileUrl("C:\\ep05", "renders_proxy/sh020/sh020_t01.mp4")).toBe(
      "http://comfy/h3pipe/file?ep=C%3A%5Cep05&path=renders_proxy%2Fsh020%2Fsh020_t01.mp4",
    );
  });

  it("turns {error} bodies into ApiError with the status", async () => {
    const { api } = fake({ "PUT /h3pipe/pick": () => json(`{"error":"sh020 t03 is failed with no mp4"}`, 409) });
    const e = await api.pick({ ep: "x", pass: "proxy", shot: "sh020", take: 3, from_pass: null }).catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(409);
    expect(e.message).toBe("sh020 t03 is failed with no mp4");
  });

  it("explains a missing route and a network failure", async () => {
    const { api } = fake({});
    await expect(api.episodes()).rejects.toThrow(/node pack loaded/);
    const broken = createHttpApi({ fetch: () => Promise.reject(new TypeError("Failed to fetch")), url: (p) => p });
    await expect(broken.getConfig()).rejects.toThrow(/Can't reach ComfyUI.*Failed to fetch/);
  });

  it("falls back from diffusion_models to unet", async () => {
    const { api } = fake({
      "GET /models/diffusion_models": () => json("[]"),
      "GET /models/unet": () => json(`["a.safetensors"]`),
    });
    expect(await api.models()).toEqual(["a.safetensors"]);
  });

  it("reads ComfyUI's queue as prompt ids", async () => {
    const { api } = fake({
      "GET /queue": () => json(`{"queue_running":[[5,"p-run",{},{}]],"queue_pending":[[6,"p-a",{}],[7,"p-b",{}]]}`),
    });
    expect(await api.comfyQueue()).toEqual({ running: ["p-run"], pending: ["p-a", "p-b"] });
  });
});
