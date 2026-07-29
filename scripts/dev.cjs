const path = require("node:path");

const root = path.resolve(__dirname, "..");
process.chdir(root);

require(path.join(root, "apps/web/node_modules/@next/env")).loadEnvConfig(
  root,
  true,
);

process.argv = [
  process.execPath,
  "next",
  "dev",
  "apps/web",
  ...process.argv.slice(2),
];
require(path.join(root, "apps/web/node_modules/next/dist/bin/next"));
