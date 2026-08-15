import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The shared packages ship TypeScript source rather than a build step, so Next
  // has to compile them alongside the app.
  transpilePackages: ["@ascras/ui", "@ascras/api-client", "@ascras/types"],
};

export default nextConfig;
