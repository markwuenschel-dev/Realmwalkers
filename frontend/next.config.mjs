/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Self-contained server output (.next/standalone/server.js + traced deps) so the Docker image can
  // run `node server.js` without shipping the full node_modules. See Dockerfile.
  output: "standalone",
  // The desk is a single-page client app whose source predates Next's ESLint rules. Typechecking is
  // run separately (tsc --noEmit); don't let lint warnings block `next build` during the migration.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
