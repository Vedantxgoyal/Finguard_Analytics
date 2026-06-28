/** @type {import('next').NextConfig} */
const nextConfig = {
  // The FastAPI backend URL is read server-side (in API route handlers /
  // server components) from process.env.BACKEND_API_URL directly — no
  // NEXT_PUBLIC_ prefix needed there, since those calls never run in the
  // browser. Client components that need to call the backend go through
  // our own Next.js API routes (see app/api/*) rather than calling FastAPI
  // directly from the browser, which avoids needing to expose the FastAPI
  // URL (and its CORS configuration) to the client at all.
  reactStrictMode: true,
  poweredByHeader: false, // don't advertise the framework in response headers
};

export default nextConfig;
