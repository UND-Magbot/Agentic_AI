# -*- coding: utf-8 -*-
"""copy-on-write 워크북 로더/세이버 — 원본 절대 미수정 보장(최우선 불변식).

설계: docs/design/fund_daily_reconcile_plan.md §3
- 원본은 read-only 로만 open, save 하지 않는다.
- 모든 쓰기는 work/ 의 복사본(shutil.copy2, 서식·메타 보존)에만.
- 안전장치: 입력이 docs/ 면 쓰기 모드 open 금지(assert), 작업 전후 원본 SHA256 동일 보장.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

import openpyxl

# 보호 디렉터리명 — 이 안의 파일은 쓰기 모드로 열지 않는다.
PROTECTED_DIR = "docs"


def sha256(path: Path) -> str:
    """파일 SHA256 해시(원본 무변경 검증용)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_protected(path: Path) -> bool:
    return PROTECTED_DIR in {p.lower() for p in path.resolve().parts}


def load_readonly(path: Path):
    """원본을 값 읽기 전용으로 연다(data_only=True, read_only=True). 절대 save 금지."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    return openpyxl.load_workbook(path, data_only=True, read_only=True)


def make_working_copy(orig: Path, out_dir: Path, *, label: str, date: str) -> Path:
    """원본을 work/ 로 복사하고 복사본 경로를 반환.

    Args:
        orig: 원본 경로(docs/ 내부).
        out_dir: 복사본 디렉터리(원본과 달라야 함).
        label: 산출 파일명 접두(예: '자금실적_FY26').
        date: 대상 일자(YYYY-MM-DD). 파일명에 포함.

    Returns:
        생성된 복사본 경로.

    Raises:
        ValueError: out_dir 이 보호 디렉터리(docs)이거나 원본과 같은 위치인 경우.
    """
    orig = Path(orig)
    out_dir = Path(out_dir)
    if _is_protected(out_dir):
        raise ValueError(f"출력 디렉터리가 보호 영역({PROTECTED_DIR})입니다: {out_dir}")
    if out_dir.resolve() == orig.resolve().parent:
        raise ValueError("복사본은 원본과 다른 디렉터리에 생성해야 합니다.")
    out_dir.mkdir(parents=True, exist_ok=True)
    runid = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = out_dir / f"{label}_{date}_{runid}{orig.suffix}"
    shutil.copy2(orig, dst)   # 서식·메타 보존 복사
    return dst


def assert_writable_copy(copy_path: Path) -> None:
    """기입 대상이 보호 디렉터리(docs) 밖의 복사본인지 보증.

    Raises:
        AssertionError: docs 내부 파일을 기입 대상으로 삼으려는 경우.
    """
    assert not _is_protected(Path(copy_path)), (
        f"원본 보호 위반: {PROTECTED_DIR} 내부 파일에 기입할 수 없습니다 — {copy_path}"
    )
