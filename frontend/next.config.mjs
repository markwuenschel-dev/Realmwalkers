import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Self-contained server output (.next/standalone/server.js + traced deps) so the Docker image can
  // run `node server.js` without shipping the full node_modules. See Dockerfile.
  output: "standalone",
  // Pin the workspace/tracing root to this app. A stray lockfile elsewhere on the machine would
  // otherwise make Turbopack infer the wrong root and mis-trace the standalone output.
  turbopack: { root: __dirname },
};

export default nextConfig;
