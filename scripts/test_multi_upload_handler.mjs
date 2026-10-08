/**
 * Composer 의 다중 업로드 핸들러 로직 격리 검증.
 *
 * React 컴포넌트 전체를 import 하지 않고, handleFileSelect 의 핵심 알고리즘만
 * 추출해 단위 테스트한다(pre-가드 / 병렬 업로드 / 결과 집계).
 */

const MAX_FILES_PER_SELECT = 20;
const MAX_FILE_BYTES = 25 * 1024 * 1024;

/**
 * 핸들러의 알고리즘 시뮬레이션.
 *
 * @param picked    선택된 파일 (name, size 필드)
 * @param uploadFn  서버로 보내는 함수 (file => { ok, data?, error? })
 * @returns { appended, error }
 */
async function simulate(picked, uploadFn) {
  // ── pre-가드 ──
  const prePassed = [];
  const preFailed = [];
  let droppedByCap = 0;
  for (const f of picked) {
    if (prePassed.length >= MAX_FILES_PER_SELECT) {
      droppedByCap += 1;
      continue;
    }
    if (f.size <= 0) {
      preFailed.push({ name: f.name, reason: "빈 파일" });
      continue;
    }
    if (f.size > MAX_FILE_BYTES) {
      const mb = Math.round(MAX_FILE_BYTES / (1024 * 1024));
      preFailed.push({ name: f.name, reason: `최대 ${mb}MB 초과` });
      continue;
    }
    prePassed.push(f);
  }
  if (droppedByCap > 0) {
    preFailed.push({
      name: `(외 ${droppedByCap}개)`,
      reason: `한 번에 최대 ${MAX_FILES_PER_SELECT}개까지 첨부 가능`,
    });
  }

  if (prePassed.length === 0) {
    const first = preFailed[0];
    return { appended: [], error: first ? `${first.name}: ${first.reason}` : null };
  }

  // ── 병렬 업로드 ──
  const results = await Promise.all(
    prePassed.map(async (file) => {
      try {
        const res = await uploadFn(file);
        if (!res.ok) return { ok: false, filename: file.name, error: res.error ?? "업로드 실패" };
        return { ok: true, data: res.data, filename: file.name };
      } catch (e) {
        return { ok: false, filename: file.name, error: e?.message ?? "오류" };
      }
    }),
  );

  const successes = results.filter((r) => r.ok);
  const failures = results.filter((r) => !r.ok);
  const appended = successes.map((s) => ({
    id: s.data.id,
    filename: s.data.filename,
    mime: s.data.mime,
    size_bytes: s.data.size_bytes,
  }));

  const allFails = [
    ...preFailed.map((p) => ({ filename: p.name, error: p.reason })),
    ...failures.map((f) => ({ filename: f.filename, error: f.error })),
  ];
  let error = null;
  if (allFails.length > 0) {
    const first = allFails[0];
    const more = allFails.length > 1 ? ` 외 ${allFails.length - 1}개` : "";
    error = `${first.filename}${more} 업로드 실패: ${first.error}`;
  }
  return { appended, error };
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

// 가짜 uploadFn — 파일명별 결과를 미리 셋업
function makeUploader(specByName) {
  return async (file) => {
    const spec = specByName[file.name];
    if (!spec || spec === "ok") {
      return {
        ok: true,
        data: {
          id: Math.floor(Math.random() * 1e6),
          filename: file.name,
          mime: "image/png",
          size_bytes: file.size,
        },
      };
    }
    if (spec === "throw") throw new Error("network");
    return { ok: false, error: spec };
  };
}

(async () => {
  // 1) 3개 정상 업로드 (병렬)
  {
    console.log("\n[case 1] 3개 정상 업로드");
    const files = [
      { name: "a.png", size: 1000 },
      { name: "b.png", size: 2000 },
      { name: "c.png", size: 3000 },
    ];
    const { appended, error } = await simulate(files, makeUploader({}));
    expect("c1.3_appended", appended.length === 3);
    expect("c1.no_error", error === null, `error=${error}`);
    expect("c1.order_preserved",
           appended[0].filename === "a.png" && appended[2].filename === "c.png");
  }

  // 2) 일부 실패 — 부분 성공 + 첫 사유 노출
  {
    console.log("\n[case 2] 일부 실패");
    const files = [
      { name: "ok1.png", size: 1000 },
      { name: "bad.png", size: 1000 },
      { name: "ok2.png", size: 1000 },
    ];
    const { appended, error } = await simulate(files, makeUploader({ "bad.png": "지원하지 않는 형식" }));
    expect("c2.2_appended", appended.length === 2);
    expect("c2.error_present", error !== null);
    expect("c2.error_filename", error?.startsWith("bad.png "), `err=${error}`);
    expect("c2.error_reason_quoted", error?.includes("지원하지 않는 형식"));
  }

  // 3) 모두 실패 — 빈 appended + 에러
  {
    console.log("\n[case 3] 모두 실패");
    const files = [
      { name: "x.png", size: 1000 },
      { name: "y.png", size: 1000 },
    ];
    const { appended, error } = await simulate(files, makeUploader({
      "x.png": "err1", "y.png": "err2",
    }));
    expect("c3.0_appended", appended.length === 0);
    expect("c3.error_count_summary", error?.includes("외 1개"), `err=${error}`);
  }

  // 4) 빈 파일 사전 거부
  {
    console.log("\n[case 4] 빈 파일 사전 거부");
    const files = [
      { name: "empty.png", size: 0 },
      { name: "good.png", size: 1000 },
    ];
    const { appended, error } = await simulate(files, makeUploader({}));
    expect("c4.1_appended", appended.length === 1);
    expect("c4.appended_only_good", appended[0].filename === "good.png");
    expect("c4.error_empty_file", error?.includes("빈 파일"));
  }

  // 5) 크기 초과 사전 거부 (25MB+)
  {
    console.log("\n[case 5] 크기 초과 사전 거부");
    const files = [
      { name: "huge.png", size: 30 * 1024 * 1024 },
      { name: "ok.png", size: 1000 },
    ];
    const { appended, error } = await simulate(files, makeUploader({}));
    expect("c5.1_appended", appended.length === 1);
    expect("c5.appended_ok", appended[0].filename === "ok.png");
    expect("c5.error_size", error?.includes("MB 초과"));
  }

  // 6) 한 번에 21개 — cap 초과 분 사전 거부
  {
    console.log("\n[case 6] 21개 한도 초과");
    const files = Array.from({ length: 21 }, (_, i) => ({
      name: `f${i}.png`,
      size: 1000,
    }));
    const { appended, error } = await simulate(files, makeUploader({}));
    expect("c6.20_appended", appended.length === 20);
    expect("c6.error_cap", error?.includes("최대 20개"));
  }

  // 7) 네트워크 throw — 그래도 다른 파일 성공
  {
    console.log("\n[case 7] 네트워크 throw + 부분 성공");
    const files = [
      { name: "throws.png", size: 1000 },
      { name: "ok.png", size: 1000 },
    ];
    const { appended, error } = await simulate(files, makeUploader({ "throws.png": "throw" }));
    expect("c7.1_appended", appended.length === 1);
    expect("c7.error_network",
           error?.startsWith("throws.png") && error?.includes("network"));
  }

  // 8) 0 파일 선택(취소) — 호출자에서 빠른 return 기대
  {
    console.log("\n[case 8] 파일 0개 — no-op");
    const { appended, error } = await simulate([], makeUploader({}));
    expect("c8.0_appended", appended.length === 0);
    expect("c8.no_error", error === null);
  }

  // 9) 정확히 20개 (한도 정확히 일치)
  {
    console.log("\n[case 9] 정확히 20개 — 모두 통과");
    const files = Array.from({ length: 20 }, (_, i) => ({
      name: `f${i}.png`,
      size: 100,
    }));
    const { appended, error } = await simulate(files, makeUploader({}));
    expect("c9.20_appended", appended.length === 20);
    expect("c9.no_error", error === null);
  }

  // 10) 사전 가드 + 업로드 가드 동시 — 둘 다 보고
  {
    console.log("\n[case 10] 사전 + 업로드 실패 합쳐 한 줄");
    const files = [
      { name: "empty.png", size: 0 },        // 사전 거부
      { name: "bad.png", size: 1000 },       // 업로드 실패
      { name: "ok.png", size: 1000 },        // 성공
    ];
    const { appended, error } = await simulate(files, makeUploader({ "bad.png": "거부" }));
    expect("c10.1_appended", appended.length === 1);
    expect("c10.error_mentions_two", error?.includes("외 1개"));
  }

  console.log(`\n==== ${PASS.length} pass / ${FAIL.length} fail ====`);
  if (FAIL.length) {
    for (const [n, d] of FAIL) console.log(`  [FAIL] ${n}: ${d}`);
    process.exit(1);
  }
  console.log("ALL OK");
})();
