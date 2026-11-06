// SPDX-License-Identifier: AGPL-3.0-or-later

const box = document.querySelector<HTMLElement>("#ai_overview.loading");
const out = box?.querySelector("p");

const run = async (box: HTMLElement, out: HTMLElement): Promise<void> => {
  const results = [...document.querySelectorAll("#urls article.result")].slice(0, 5).map((el) => ({
    title: el.querySelector("h3")?.textContent?.trim() ?? "",
    url: el.querySelector<HTMLAnchorElement>("h3 a")?.href ?? "",
    content: el.querySelector(".content")?.textContent?.trim() ?? ""
  }));
  const q = document.querySelector<HTMLInputElement>("#q")?.value ?? "";
  const body = new URLSearchParams({ q, results: JSON.stringify(results) });

  const res = await fetch("./ai_overview", { method: "POST", body, signal: AbortSignal.timeout(90_000) });
  if (!res.ok || !res.body) throw new Error(res.statusText);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  for (let r = await reader.read(); !r.done; r = await reader.read()) {
    box.classList.remove("loading");
    out.textContent += r.value;
  }
  box.classList.toggle("error", out.textContent?.startsWith("⚠") ?? false);
  if (!out.textContent) box.remove();
};

if (box && out) run(box, out).catch(() => box.remove());
export {};
