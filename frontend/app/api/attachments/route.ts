/**
 * 첨부 업로드 프록시 — multipart 를 재구성해 backend 로 전달.
 *
 * POST /api/attachments → backend POST /v1/attachments
 *
 * httpOnly 쿠키 토큰을 Authorization 헤더로 변환. 큰 multipart body(예: 10MB 이상 m4a)는
 * ReadableStream 그대로 forward 시 Node fetch 의 boundary/chunk 처리에서 종종 깨져
 * FastAPI 가 422(file 필드 누락)를 반환한다. 안전하게 formData() 로 파싱 후 새 FormData
 * 로 재구성해서 보내면 fetch 가 boundary 를 다시 생성하므로 견고하다.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
// 25MB 까지 허용 — 백엔드의 attachment_max_bytes 와 일치.
export const maxDuration = 60;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

export async function POST(req: NextRequest) {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  }

  const contentType = req.headers.get("content-type") ?? "";
  if (!contentType.startsWith("multipart/form-data")) {
    return NextResponse.json(
      { detail: "multipart/form-data 가 필요합니다." },
      { status: 400 },
    );
  }

  let incoming: FormData;
  try {
    incoming = await req.formData();
  } catch (e) {
    return NextResponse.json(
      { detail: `multipart 파싱 실패: ${e instanceof Error ? e.message : String(e)}` },
      { status: 400 },
    );
  }

  const file = incoming.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json(
      { detail: "file 필드가 누락되었습니다." },
      { status: 400 },
    );
  }

  // 새 FormData 로 재구성 — fetch 가 boundary 를 다시 생성하므로 Content-Type 헤더는 넘기지 않는다.
  const outgoing = new FormData();
  outgoing.append("file", file, file.name);

  const r = await fetch(`${BACKEND_URL}/v1/attachments`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: outgoing,
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
