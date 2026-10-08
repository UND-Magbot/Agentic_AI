/**
 * frontend message.tsx 의 renderBodyWithDownloads 동작 검증 (Node 환경 격리).
 *
 * 두 패턴을 모두 검증:
 *   1) Markdown link: `[파일명.xlsx](/api/attachments/N/download)` → 카드 노드
 *   2) Bare URL: `/api/attachments/N/download` → 인라인 anchor
 *
 * (실제 React 컴포넌트는 import 하지 않고 동일한 정규식·분리 로직을 재현.)
 */

const _MARKDOWN_DOWNLOAD_RE =
  /\[([^\]\n]+?)\]\(\/(?:api|v1)\/attachments\/(\d+)\/download\)/g;
const _BARE_DOWNLOAD_URL_RE = /\/(?:api|v1)\/attachments\/(\d+)\/download/g;

function renderMarked(body) {
  if (!body) return body;

  const cards = [];
  _MARKDOWN_DOWNLOAD_RE.lastIndex = 0;
  let m;
  while ((m = _MARKDOWN_DOWNLOAD_RE.exec(body)) !== null) {
    cards.push({
      start: m.index,
      end: m.index + m[0].length,
      filename: m[1].trim(),
      id: m[2],
    });
  }

  const bares = [];
  _BARE_DOWNLOAD_URL_RE.lastIndex = 0;
  let mb;
  while ((mb = _BARE_DOWNLOAD_URL_RE.exec(body)) !== null) {
    const s = mb.index;
    const e = s + mb[0].length;
    if (cards.some((c) => s >= c.start && e <= c.end)) continue;
    bares.push({ start: s, end: e, id: mb[1] });
  }

  if (cards.length === 0 && bares.length === 0) return body;

  const points = [
    ...cards.map((c) => ({ kind: "card", ...c })),
    ...bares.map((b) => ({ kind: "bare", ...b })),
  ].sort((a, b) => a.start - b.start);

  const out = [];
  let cursor = 0;
  for (const p of points) {
    if (p.start > cursor) out.push(body.slice(cursor, p.start));
    if (p.kind === "card") {
      out.push({
        type: "card",
        filename: p.filename,
        href: `/api/attachments/${p.id}/download`,
        id: p.id,
      });
    } else {
      out.push({
        type: "anchor",
        href: `/api/attachments/${p.id}/download`,
        text: body.slice(p.start, p.end),
        id: p.id,
      });
    }
    cursor = p.end;
  }
  if (cursor < body.length) out.push(body.slice(cursor));
  return out;
}

const PASS = [];
const FAIL = [];

function expect(name, cond, detail = "") {
  if (cond) {
    PASS.push(name);
    console.log(`  PASS ${name}`);
  } else {
    FAIL.push([name, detail]);
    console.log(`  FAIL ${name} — ${detail}`);
  }
}

// 1) Markdown link → 카드
{
  console.log("\n[case 1] markdown link → 카드 노드");
  const body = "✓ Expense 양식이 생성되었습니다.\n\n[(주)유엔디_expense02월_배재병.xlsx](/api/attachments/26/download)";
  const out = renderMarked(body);
  const cards = out.filter((p) => typeof p === "object" && p.type === "card");
  expect("c1.one_card", cards.length === 1);
  expect("c1.filename", cards[0].filename === "(주)유엔디_expense02월_배재병.xlsx");
  expect("c1.href", cards[0].href === "/api/attachments/26/download");
  expect("c1.id", cards[0].id === "26");
}

// 2) Bare URL (구 버전 호환) → 작은 anchor
{
  console.log("\n[case 2] bare URL → inline anchor");
  const body = "다운로드: /api/attachments/9/download";
  const out = renderMarked(body);
  const anchors = out.filter((p) => typeof p === "object" && p.type === "anchor");
  expect("c2.one_anchor", anchors.length === 1);
  expect("c2.text_preserved", anchors[0].text === "/api/attachments/9/download");
}

// 3) /v1/... 구버전 경로도 anchor 로 변환
{
  console.log("\n[case 3] /v1/... 구 버전 경로");
  const body = "다운로드: /v1/attachments/42/download";
  const out = renderMarked(body);
  const anchors = out.filter((p) => typeof p === "object" && p.type === "anchor");
  expect("c3.one_anchor", anchors.length === 1);
  expect("c3.href_rewritten", anchors[0].href === "/api/attachments/42/download");
  expect("c3.text_keeps_v1", anchors[0].text === "/v1/attachments/42/download");
}

// 4) markdown + 주변 텍스트 — 텍스트 보존
{
  console.log("\n[case 4] markdown + 주변 텍스트");
  const body = "전:HELLO [file.xlsx](/api/attachments/5/download) :WORLD";
  const out = renderMarked(body);
  expect("c4.before_text", out[0] === "전:HELLO ");
  expect("c4.card", typeof out[1] === "object" && out[1].type === "card");
  expect("c4.after_text", out[2] === " :WORLD");
}

// 5) bare URL 가 markdown link 안에 있어도 중복 매치 안 함
{
  console.log("\n[case 5] markdown 내 bare URL 중복 매치 방지");
  const body = "[a.xlsx](/api/attachments/1/download)";
  const out = renderMarked(body);
  const cards = out.filter((p) => typeof p === "object" && p.type === "card");
  const anchors = out.filter((p) => typeof p === "object" && p.type === "anchor");
  expect("c5.one_card", cards.length === 1);
  expect("c5.no_bare_anchor", anchors.length === 0);
}

// 6) 여러 markdown link 한 본문에
{
  console.log("\n[case 6] 여러 markdown link");
  const body = "[a.xlsx](/api/attachments/1/download)\n[b.xlsx](/api/attachments/2/download)";
  const out = renderMarked(body);
  const cards = out.filter((p) => typeof p === "object" && p.type === "card");
  expect("c6.two_cards", cards.length === 2);
  expect("c6.ids_in_order", cards[0].id === "1" && cards[1].id === "2");
}

// 7) URL 없는 본문 → 그대로 통과
{
  console.log("\n[case 7] URL 없는 본문");
  const body = "안녕하세요. 무엇을 도와드릴까요?";
  const out = renderMarked(body);
  expect("c7.passthrough", out === body);
}

// 8) 실제 v1 fix 후 short_message (markdown + warning + 안내문 없음)
{
  console.log("\n[case 8] 실제 success short_message");
  const body =
    "✓ Expense 양식이 생성되었습니다.\n\n" +
    "[(주)유엔디_expense02월_배재병.xlsx](/api/attachments/26/download)";
  const out = renderMarked(body);
  const cards = out.filter((p) => typeof p === "object" && p.type === "card");
  expect("c8.one_card", cards.length === 1);
  expect("c8.filename", cards[0].filename === "(주)유엔디_expense02월_배재병.xlsx");
  expect("c8.href", cards[0].href === "/api/attachments/26/download");
  // 앞부분 "✓ Expense ..." 텍스트 보존
  expect("c8.intro_text_intact",
         typeof out[0] === "string" && out[0].includes("양식이 생성되었습니다"));
}

// 9) 빈 본문
{
  console.log("\n[case 9] 빈 본문");
  expect("c9.empty", renderMarked("") === "");
  expect("c9.null", renderMarked(null) === null);
}

// 10) 비매칭 패턴
{
  console.log("\n[case 10] 비매칭 패턴");
  expect("c10.no_match_info", renderMarked("/api/attachments/9/info") === "/api/attachments/9/info");
  expect("c10.no_match_bracket", renderMarked("[a.xlsx](no-url)") === "[a.xlsx](no-url)");
}

console.log(`\n==== ${PASS.length} pass / ${FAIL.length} fail ====`);
if (FAIL.length) {
  for (const [name, detail] of FAIL) console.log(`  [FAIL] ${name}: ${detail}`);
  process.exit(1);
}
console.log("ALL OK");
