/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The desk is a single-page client app whose source predates Next's ESLint rules. Typechecking is
  // run separately (tsc --noEmit); don't let lint warnings block `next build` during the migration.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
