import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next.js dev 모드의 좌하단 N 로고/빌드 상태 indicator 비활성.
  devIndicators: false,
  // Next 16 기본 동작은 trailing slash 없는 요청을 308 로 `/path/` 로 redirect 하고,
  // redirect 된 경로에서 `[id]/download` 같은 nested dynamic route 매칭이 실패해 404 가 난다
  // (관측: `/api/attachments/{id}/download` → 308 `/api/attachments/{id}/download/` → 404).
  // 둘 다 받아 들이도록 redirect 자체를 비활성화 — 기존 호출부의 URL 형식을 보존한다.
  skipTrailingSlashRedirect: true,
  // 다른 PC 에서 http://<이 PC IP>:3010 으로 접속할 때 dev 서버가 /_next 리소스(HMR 등)를
  // cross-origin 으로 막지 않게 허용할 호스트. .env 의 ALLOWED_DEV_ORIGINS(쉼표 구분)로 지정.
  allowedDevOrigins: (process.env.ALLOWED_DEV_ORIGINS ?? "")
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean),
  // Next 16 의 proxy(구 middleware) 가 client body 를 기본 10MB 까지만 버퍼링한다.
  // 회의 녹음(m4a) · 영수증 다발 업로드는 이 한도를 넘을 수 있어 잘린 body 가 route 까지 흘러가
  // FastAPI 에서 422("file 필드 누락") 가 발생했다. 백엔드의 ATTACHMENT_MAX_BYTES(75MB) 와 동기.
  experimental: {
    proxyClientMaxBodySize: "75mb",
  },
};

export default nextConfig;
