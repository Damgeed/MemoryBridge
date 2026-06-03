import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  images: {
    domains: ["memorybridge.app", "avatars.githubusercontent.com", "lh3.googleusercontent.com"],
  },
};

export default nextConfig;
