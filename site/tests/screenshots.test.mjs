import assert from "node:assert/strict";
import { stat, readFile } from "node:fs/promises";
import test from "node:test";

const screenshots = [
  {
    page: "getting-started/overview",
    names: ["run-dashboard", "new-run"],
  },
  {
    page: "workflows/specification-delivery",
    names: ["specification-workflow"],
  },
  {
    page: "workflows/pull-request-review",
    names: ["pull-request-review"],
  },
];

test("documentation publishes the application screenshots", async () => {
  for (const screenshot of screenshots) {
    const html = await readFile(`dist/${screenshot.page}/index.html`, "utf8");
    for (const name of screenshot.names) {
      const image = await stat(`dist/images/app/${name}.webp`);
      assert.ok(image.size > 10_000, `${name} must be a real optimized screenshot`);
      assert.match(html, new RegExp(`/images/app/${name}\\.webp`));
    }
  }
});
