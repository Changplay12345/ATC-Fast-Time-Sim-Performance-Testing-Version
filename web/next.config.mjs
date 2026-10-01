/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Keep `next build` and `next dev` in SEPARATE output dirs. They both
  // write to `.next` by default, so running a build while the dev server
  // is live corrupts its webpack chunks (MODULE_NOT_FOUND './xxx.js').
  // The `build` npm script sets NEXT_DIST_DIR=.next-build; dev uses `.next`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The desktop app ships the front end as plain static files inside the
  // installer (no Node server). Everything here is client-rendered and the
  // engine is reached over HTTP, so a static export loses nothing. Only set
  // for desktop builds; Vercel keeps the default output.
  ...(process.env.DESKTOP_BUILD === "1"
    ? { output: "export", images: { unoptimized: true } }
    : {}),
};

export default nextConfig;
