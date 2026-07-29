import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readOutput = (path) => readFile(new URL(`../dist/${path}`, import.meta.url), "utf8");

test("shared top bar identifies mafia", async () => {
  const [docsPage, notFoundPage] = await Promise.all([
    readOutput("getting-started/overview/index.html"),
    readOutput("404.html")
  ]);

  for (const html of [docsPage, notFoundPage]) {
    assert.match(html, /class="dk-crumb"[^>]*aria-label="Project: mafia"[^>]*>\/ mafia<\/span>/);
    assert.doesNotMatch(html, /class="dk-crumb"[^>]*>\/ docs<\/span>/);
  }
});
