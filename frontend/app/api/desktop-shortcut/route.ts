// 바탕화면 바로가기 설치 파일(UND Cortex 설치.cmd) 내려받기 — 프로필 메뉴 [바탕화면 바로가기 만들기](사용자 2026-10-29).
// 한 번 더블클릭하면 로고 아이콘(/und-cortex.ico, scripts/make_desktop_icon.py)을 내 PC(%LOCALAPPDATA%\UND Cortex)에 받아 두고
// 그 아이콘을 쓰는 바로가기(UND Cortex.url)를 바탕화면에 만든다 — Windows 는 인터넷 주소의 아이콘을 받아 오지 않아서(실측) PC 에 둔다.
// 주소는 고정하지 않고 지금 접속한 주소(운영 서버에서 받으면 운영 주소)를 넣는다. 관리자 권한·프로그램 설치 없음.

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { NextResponse, type NextRequest } from "next/server";

export const runtime = "nodejs";

const FILE_NAME = "UND Cortex 설치.cmd";
// 호스트 머리글을 그대로 스크립트에 넣으므로 이름·IP·포트 모양만 받는다(cmd 특수문자가 끼어들 수 없게)
const HOST_RE = /^[A-Za-z0-9.-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:]+\](:\d{1,5})?$/;

function origin(req: NextRequest): string | null {
  const host = (req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? "").split(",")[0].trim();
  const proto = (req.headers.get("x-forwarded-proto") ?? req.nextUrl.protocol.replace(":", "")).split(",")[0].trim();
  if (!HOST_RE.test(host) || (proto !== "http" && proto !== "https")) return null;
  return `${proto}://${host}`;
}

// 아이콘이 바뀌면 PC 에 저장하는 파일 이름도 바뀌게(내용 지문) — Windows 는 같은 경로의 아이콘을 캐시에서 계속 보여 준다
// (사용자 2026-10-29: 로고를 바꾸고 다시 실행했는데 아이콘이 그대로)
function iconVersion(): string {
  try {
    const data = readFileSync(path.join(process.cwd(), "public", "und-cortex.ico"));
    return createHash("sha1").update(data).digest("hex").slice(0, 8);
  } catch {
    return "1";
  }
}

function script(base: string): string {
  const icon = `und-cortex-${iconVersion()}.ico`;
  return [
    "@echo off",
    "chcp 65001 >nul",
    "setlocal",
    `set "URL=${base}/"`,
    `set "ICON_URL=${base}/und-cortex.ico"`,
    'set "DIR=%LOCALAPPDATA%\\UND Cortex"',
    `set "ICON=%DIR%\\${icon}"`,
    'if not exist "%DIR%" mkdir "%DIR%"',
    'curl.exe -fsSL "%ICON_URL%" -o "%ICON%" >nul 2>&1',
    // 예전 아이콘 파일은 지운다
    `for %%F in ("%DIR%\\und-cortex*.ico") do if /i not "%%~nxF"=="${icon}" del "%%F" >nul 2>&1`,
    // 바탕화면이 OneDrive 로 옮겨진 PC 도 있어 실제 바탕화면 위치를 묻는다
    "for /f \"usebackq delims=\" %%D in (`powershell -NoProfile -Command \"[Environment]::GetFolderPath('Desktop')\"`) do set \"DESK=%%D\"",
    'if not defined DESK set "DESK=%USERPROFILE%\\Desktop"',
    "(",
    "  echo [InternetShortcut]",
    "  echo URL=%URL%",
    '  if exist "%ICON%" echo IconFile=%ICON%',
    '  if exist "%ICON%" echo IconIndex=0',
    ') > "%DESK%\\UND Cortex.url"',
    "ie4uinit.exe -show >nul 2>&1", // 바탕화면 아이콘 캐시 새로 고침
    "echo.",
    'if exist "%DESK%\\UND Cortex.url" (',
    "  echo 바탕화면에 UND Cortex 바로가기를 만들었습니다. 이제 아이콘을 더블클릭해 여세요.",
    ") else (",
    "  echo 바로가기를 만들지 못했습니다. 담당자에게 알려 주세요.",
    ")",
    "timeout /t 4 >nul",
    "",
  ].join("\r\n");
}

export function GET(req: NextRequest) {
  const base = origin(req);
  if (!base) return NextResponse.json({ error: "접속 주소를 확인하지 못했습니다." }, { status: 400 });
  return new NextResponse(script(base), {
    headers: {
      "Content-Type": "application/octet-stream",
      "Content-Disposition": `attachment; filename="UND_Cortex_setup.cmd"; filename*=UTF-8''${encodeURIComponent(FILE_NAME)}`,
      "Cache-Control": "no-store",
    },
  });
}
