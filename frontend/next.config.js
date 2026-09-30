/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Proxy API calls to the FastAPI backend so the browser hits same-origin.
    const backend = process.env.BACKEND_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

module.exports = nextConfig;
