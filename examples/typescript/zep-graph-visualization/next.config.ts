import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Example UI uses intentional D3 typing escapes; keep `next build` focused on
  // TypeScript + production bundling rather than lint-as-errors.
  eslint: {
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
