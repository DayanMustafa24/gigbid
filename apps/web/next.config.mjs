/** Same-origin proxy: the browser only talks to /api/* (session cookie is first-party, no CORS), Next forwards to FastAPI.
 *  compress:false keeps Server-Sent Events unbuffered. API_URL is the FastAPI base (docker compose: http://api:8000; read at BUILD time). */
import path from "node:path";
import { fileURLToPath } from "node:url";

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.dirname(fileURLToPath(import.meta.url)), // standalone server.js lands at the image root, not in a nested folder
  compress: false,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
