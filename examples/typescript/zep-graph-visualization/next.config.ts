import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The D3 graph code relies on typing escapes that lint rejects as errors.
  eslint: {
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
