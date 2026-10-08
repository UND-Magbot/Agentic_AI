"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

type LoginField = "username" | "password";

type LoginResponse = {
  ok: boolean;
  detail?: string;
  field?: LoginField;
  reason?: string;
  user?: { username?: string };
};

type FieldError = { field: LoginField | "form"; message: string };

export function LoginForm({ nextPath }: { nextPath: string }) {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState<FieldError | null>(null);
  const [loading, setLoading] = useState(false);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  // 입력이 변경되면 그 필드의 에러는 자동 해제 (form-level 에러는 다음 제출 시 갱신).
  // 단, 빈 문자열로의 변경은 사용자 입력이 아닌 보안상 비움(setPassword("")) 케이스가 있어
  // 빈 값에서는 자동 해제하지 않음 — 그렇지 않으면 비밀번호 오류 메시지가 1프레임 후 사라진다.
  useEffect(() => {
    if (!username) return;
    if (error?.field === "username") setError(null);
  }, [username]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!password) return;
    if (error?.field === "password") setError(null);
  }, [password]); // eslint-disable-line react-hooks/exhaustive-deps

  // 마우스 커서를 시스템 wait(라운드 링)으로 즉시 전환/복구하는 헬퍼.
  // body 에 inline cursor 만 주면 button/input 의 자체 cursor 가 우선해서 mouse 가
  // 그 위에 있을 땐 wait 이 안 보인다. globals.css 의 `body.und-busy *` 룰이
  // !important 로 모든 자손을 덮어쓰므로 클래스 토글이 가장 안정적.
  // React 리렌더 사이클을 거치지 않으므로 클릭 즉시 반영된다.
  function setBusyCursor(busy: boolean) {
    if (typeof document === "undefined") return;
    document.body.classList.toggle("und-busy", busy);
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (loading) return;

    // 1) 클라이언트 측 1차 검증 — 아이디 빈값을 가장 먼저 본다(우선순위 최상),
    //    그 다음 비밀번호 빈값. 그 외 모든 케이스(짧은 비번/잘못된 비번/아이디 없음)는
    //    백엔드에 보내서 user_not_found → wrong_password 순서로 평가되도록 한다.
    if (!username.trim()) {
      setError({ field: "username", message: "아이디를 입력해주세요." });
      usernameRef.current?.focus();
      return;
    }
    if (!password) {
      setError({ field: "password", message: "비밀번호를 입력해주세요." });
      passwordRef.current?.focus();
      return;
    }

    // React 리렌더 전에 커서를 wait 으로 — 사용자가 즉시 로딩 상태를 인지.
    setBusyCursor(true);
    setError(null);
    setLoading(true);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const data: LoginResponse = await r.json().catch(() => ({ ok: false }));
      if (!r.ok || !data.ok) {
        // 매핑되지 않은 케이스(form-level 에러, 네트워크 stub 등)는 비밀번호 input 아래에 표시.
        // 별도 banner UI 를 두지 않고, 메시지도 "비밀번호가 일치하지 않습니다" 로 통일한다.
        const field: FieldError["field"] = data.field ?? "password";
        const message =
          typeof data.detail === "string" && data.detail.trim()
            ? data.detail
            : "비밀번호가 일치하지 않습니다.";
        setError({ field, message });
        // 문제 필드 포커스 + 비밀번호는 보안상 비움.
        if (field === "username") {
          usernameRef.current?.select();
        } else {
          setPassword("");
          passwordRef.current?.focus();
        }
        setBusyCursor(false);
        setLoading(false);
        return;
      }
      // 성공 — 라우팅 진행 중에도 wait 커서를 유지하다가, 페이지 이동이 끝나면 자연 사라짐.
      // 다만 같은 도메인 내 client-nav 에서는 body 가 살아있으므로 명시적으로 한 번 풀어준다.
      setBusyCursor(false);
      router.replace(nextPath || "/");
      router.refresh();
    } catch (err) {
      // 네트워크/서버 다운 등 — 사용자 입장에선 인증 실패와 구분이 어려우므로 같은 문구로 통일.
      // 디버깅용 원문 메시지는 콘솔에만 남긴다.
      if (typeof console !== "undefined") {
        console.warn("[login] network error:", err);
      }
      setError({ field: "password", message: "비밀번호가 일치하지 않습니다." });
      setPassword("");
      passwordRef.current?.focus();
      setBusyCursor(false);
      setLoading(false);
    }
  }

  // 안전망 — 컴포넌트 언마운트 시 busy 클래스를 항상 정리. 도중에 라우트가 바뀌어도
  // 잔존 wait 커서가 새 페이지에 남지 않도록.
  useEffect(() => {
    return () => {
      if (typeof document !== "undefined") document.body.classList.remove("und-busy");
    };
  }, []);

  const usernameInvalid = error?.field === "username";
  const passwordInvalid = error?.field === "password";

  return (
    <form
      onSubmit={onSubmit}
      // 로딩 중엔 폼 전체가 wait 커서(시스템 로딩 링) — 금지 표시(not-allowed)는 인증 실패처럼 보여 부자연스러움.
      className={cn("flex flex-col gap-4", loading && "cursor-wait")}
      aria-busy={loading}
      noValidate
    >
      <Field
        label="아이디"
        htmlFor="username"
        error={usernameInvalid ? error.message : undefined}
      >
        <input
          ref={usernameRef}
          id="username"
          type="text"
          autoComplete="username"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          disabled={loading}
          aria-invalid={usernameInvalid}
          aria-describedby={usernameInvalid ? "err-username" : undefined}
          className={cn(inputClass, usernameInvalid && inputErrorClass)}
        />
      </Field>

      <Field
        label="비밀번호"
        htmlFor="password"
        error={passwordInvalid ? error.message : undefined}
      >
        <div className="relative">
          <input
            ref={passwordRef}
            id="password"
            type={showPw ? "text" : "password"}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
            aria-invalid={passwordInvalid}
            aria-describedby={passwordInvalid ? "err-password" : undefined}
            className={cn(inputClass, "pr-10", passwordInvalid && inputErrorClass)}
          />
          <button
            type="button"
            onClick={() => setShowPw((v) => !v)}
            aria-label={showPw ? "비밀번호 숨기기" : "비밀번호 표시"}
            tabIndex={-1}
            className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
          >
            <Icon name={showPw ? "eye-off" : "eye"} className="h-4 w-4" />
          </button>
        </div>
      </Field>

      <button
        type="submit"
        disabled={loading}
        className={cn(
          "mt-1 inline-flex h-11 items-center justify-center rounded-xl text-[14px] font-semibold tracking-tight transition",
          "und-grad text-white shadow-[0_10px_24px_-10px_rgba(37,99,235,0.65),inset_0_1px_0_rgba(255,255,255,0.35)]",
          "hover:brightness-105 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          // 로딩 중엔 wait 커서(시스템 로딩 링), 그 외 disabled 케이스가 생기면 not-allowed.
          loading ? "cursor-wait opacity-70" : "disabled:cursor-not-allowed disabled:opacity-60",
        )}
      >
        {loading ? (
          <span className="inline-flex items-center gap-2">
            <Spinner />
            로그인 중...
          </span>
        ) : (
          "로그인"
        )}
      </button>
    </form>
  );
}

const inputClass = cn(
  "h-10 w-full rounded-md bg-surface px-3 text-[14px] text-foreground placeholder:text-foreground-subtle/70",
  "border border-border focus:border-foreground/40 focus:outline-none",
  "focus:ring-2 focus:ring-[color:var(--accent-ring)]",
  // 폼 wrapper 의 cursor-wait 가 disabled input 에도 상속되도록 cursor 지정을 빼고
  // 시각 피드백은 opacity 만 유지. (disabled 가 로딩 외 다른 컨텍스트에서 발생할 일이 거의 없음.)
  "disabled:opacity-60",
);

// invalid 상태 — 두꺼운 빨간 보더 + 빨간 포커스 링. 메인 inputClass 위에 덮어씀.
const inputErrorClass = cn(
  "border-2 border-red-500 focus:border-red-500 focus:ring-red-500/30",
);

function Field({
  label,
  htmlFor,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={htmlFor}
        className="font-sans text-[12.5px] font-medium tracking-tight text-foreground"
      >
        {label}
      </label>
      {children}
      {error && (
        <p
          id={`err-${htmlFor}`}
          role="alert"
          aria-live="polite"
          className="mt-1 text-[12.5px] font-medium text-red-600 dark:text-red-400"
        >
          {error}
        </p>
      )}
    </div>
  );
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" opacity="0.25" />
      <path d="M21 12a9 9 0 0 1-9 9" />
    </svg>
  );
}
