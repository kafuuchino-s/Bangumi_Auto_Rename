import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 静态导出，由 FastAPI StaticFiles 托管（单端口 5999 合一）
  output: "export",
  // 静态导出用目录路由，加 trailing slash 便于 FastAPI 回退到 index.html
  trailingSlash: true,
  // 图片不优化（静态导出无服务端优化）
  images: { unoptimized: true },
  // 开发期：把 /api/* 代理到 Python 后端 5999（生产由 FastAPI 同源托管，相对路径即可）
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:5999/api/:path*",
      },
    ];
  },
};

export default nextConfig;
