import type { NextConfig } from "next";

const isDesktop = (process.env.NEXT_PUBLIC_PLATFORM || "").toLowerCase() === "desktop";

const nextConfig: NextConfig = isDesktop
  ? {
      output: "export",
      images: { unoptimized: true },
    }
  : {
      async rewrites() {
        const api = (
          process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8787"
        ).replace("://localhost", "://127.0.0.1");
        return [
          {
            source: "/backend/:path*",
            destination: `${api}/:path*`,
          },
        ];
      },
    };

export default nextConfig;
