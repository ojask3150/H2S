/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Proxy API calls to the FastAPI backend so the browser hits same-origin.
    const configuredBackend = process.env.BACKEND_URL || "http://localhost:8000";
    const backend = /^https?:\/\//.test(configuredBackend)
      ? configuredBackend
      : `https://${configuredBackend}`;
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

module.exports = nextConfig;
