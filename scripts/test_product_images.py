"""제품 이미지 라이브러리 검증 — 소개서 쪽 그림 ↔ 제품 짝짓기, 컨셉 대안 → 제품 사진 고르기. 모델·DB 불필요.

쪽 XML 은 실제 소개서 22쪽 구조를 줄여 옮겼다(AMR 5종 사진이 사양표 열 위에 한 줄로, 표 머리칸에 제품 이름).
"""
import io
import sys
import zipfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from PIL import Image  # noqa: E402

from app.company_knowledge import product_images as pi  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


NS = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
E = pi.EMU_PER_PT


def pic(rid: str, x: float, y: float, w: float, h: float) -> str:
    return (f'<p:pic><p:blipFill><a:blip r:embed="{rid}"/></p:blipFill><p:spPr><a:xfrm>'
            f'<a:off x="{int(x * E)}" y="{int(y * E)}"/><a:ext cx="{int(w * E)}" cy="{int(h * E)}"/></a:xfrm></p:spPr></p:pic>')


def sp(txt: str, x: float, y: float, w: float, h: float) -> str:
    return (f'<p:sp><p:spPr><a:xfrm><a:off x="{int(x * E)}" y="{int(y * E)}"/><a:ext cx="{int(w * E)}" cy="{int(h * E)}"/>'
            f'</a:xfrm></p:spPr><p:txBody><a:p><a:r><a:t>{txt}</a:t></a:r></a:p></p:txBody></p:sp>')


def table(x: float, y: float, cols: list[float], rows: list[list[str]], rh: float = 20) -> str:
    grid = "".join(f'<a:gridCol w="{int(c * E)}"/>' for c in cols)
    trs = "".join(f'<a:tr h="{int(rh * E)}">' + "".join(
        f"<a:tc><a:txBody><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:txBody></a:tc>" for c in r) + "</a:tr>" for r in rows)
    return (f'<p:graphicFrame><p:xfrm><a:off x="{int(x * E)}" y="{int(y * E)}"/><a:ext cx="{int(sum(cols) * E)}" cy="0"/>'
            f'</p:xfrm><a:graphic><a:graphicData><a:tbl><a:tblGrid>{grid}</a:tblGrid>{trs}</a:tbl></a:graphicData></a:graphic>'
            f'</p:graphicFrame>')


def slide(body: str) -> bytes:
    return f'<p:sld {NS}><p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>'.encode()


def rels(targets: dict[str, str]) -> bytes:
    rs = "".join(f'<Relationship Id="{k}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                 f'Target="../media/{v}"/>' for k, v in targets.items())
    return f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rs}</Relationships>'.encode()


# ── 1) 22쪽: 사진 5장 ↔ 사양표 머리칸 5개(열 중심과 사진 중심이 맞는다) ─────────────
names22 = ["SLIM AMR", "리프팅 AMR", "롤러컨베이어 AMR", "리프트컨베이어 AMR", "Gantry 이송 AMR"]
pics22 = [(175, 86), (289, 121), (412, 137), (558, 110), (698, 92)]      # (x, w) — 실제 22쪽 값
body = sp("magbot_ 이동형 로봇 AMR 제품군", 23, 12, 276, 29) + pic("rLogo", 680, 0, 160, 50)
body += "".join(pic(f"r{i}", x, 82, w, 118) for i, (x, w) in enumerate(pics22))
body += table(31, 216, [122, 131, 131, 131, 131, 131], [["항목", *names22], ["치수", "870", "740", "920", "맞춤", "맞춤"]])
lay = pi.parse_slide(slide(body), rels({"rLogo": "image87.png", **{f"r{i}": f"image{137 + i}.png" for i in range(5)}}), 22)
check(len(lay.pics) == 6, "22쪽 그림 6장(로고 포함) 읽음", str(len(lay.pics)))
cards22 = {21 + i: n for i, n in enumerate(names22)}
m = pi.match_pics(lay, cards22, skip_media={"ppt/media/image87.png"})
got = {cid: [p.media.rsplit("/", 1)[-1] for p in ps] for cid, ps in m.items()}
check(got == {21 + i: [f"image{137 + i}.png"] for i in range(5)}, "AMR 5종 사진이 각자 제품에 짝지어짐", str(got))

# 쪽 제목처럼 제품 이름이 여럿 든 글은 위치 근거가 아니다 — 머리칸이 없으면 짝을 짓지 않는다.
lay_t = pi.parse_slide(slide(sp("SLIM AMR · 리프팅 AMR 비교", 100, 20, 300, 30) + pic("r0", 175, 82, 86, 118)),
                       rels({"r0": "a.png"}), 5)
check(pi.match_pics(lay_t, {21: "SLIM AMR", 22: "리프팅 AMR"}, set()) == {}, "이름이 여럿 든 제목은 근거로 안 씀")

# ── 2) 제품 하나인 쪽: 큰 그림부터 PER_CARD_MAX 장, 작은 아이콘·EMF 제외 ───────────────
body1 = (pic("a", 10, 10, 300, 200) + pic("b", 400, 10, 100, 100) + pic("c", 520, 10, 30, 30)
         + pic("d", 600, 10, 200, 200) + pic("e", 10, 300, 120, 120) + pic("f", 200, 300, 150, 150))
lay1 = pi.parse_slide(slide(body1), rels({"a": "a.png", "b": "b.png", "c": "c.png", "d": "d.emf",
                                          "e": "e.jpeg", "f": "f.png"}), 26)
m1 = pi.match_pics(lay1, {136: "포크리프트 AMR"}, set())
check([p.media.rsplit("/", 1)[-1] for p in m1[136]] == ["a.png", "f.png", "e.jpeg"],
      "제품 하나: 큰 그림 순 3장, 아이콘(30pt)·EMF 제외", str(m1))

# ── 3) 그룹 안 그림은 그룹 변환(축소·이동)을 반영한다 ─────────────────────────────
grp = ('<p:grpSp><p:grpSpPr><a:xfrm><a:off x="{ox}" y="0"/><a:ext cx="{ex}" cy="{ex}"/><a:chOff x="0" y="0"/>'
       '<a:chExt cx="{cx}" cy="{cx}"/></a:xfrm></p:grpSpPr>{inner}</p:grpSp>').format(
    ox=int(500 * E), ex=int(100 * E), cx=int(200 * E), inner=pic("g", 0, 0, 200, 200))
lay_g = pi.parse_slide(slide(grp), rels({"g": "g.png"}), 3)
p0 = lay_g.pics[0]
check((round(p0.x), round(p0.w)) == (500, 100), "그룹 변환 반영(위치 500pt, 크기 절반)", f"{p0.x},{p0.w}")

# ── 4) 여러 쪽에 반복되는 그림(로고) 찾기 ─────────────────────────────────────────
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    for n in range(1, 6):
        z.writestr(f"ppt/slides/_rels/slide{n}.xml.rels",
                   rels({"L": "logo.png", **({"X": f"only{n}.png"} if n < 3 else {})}))
with zipfile.ZipFile(buf) as z:
    rep = pi.repeated_media(z)
check(rep == {"ppt/media/logo.png"}, "4쪽 이상 반복되는 그림만 로고로 봄", str(rep))

# ── 5) 그림 변환: 투명 배경은 흰색, 긴 변 제한, 너무 작은 그림은 버림 ───────────────
im = Image.new("RGBA", (2000, 1000), (0, 0, 0, 0))
im.paste((200, 0, 0, 255), (0, 0, 1000, 1000))
b = io.BytesIO()
im.save(b, "PNG")
jpeg, w, h = pi.to_jpeg(b.getvalue())
px = Image.open(io.BytesIO(jpeg)).convert("RGB").getpixel((w - 5, h // 2))
check((w, h) == (pi.MAX_EDGE, pi.MAX_EDGE // 2) and min(px) > 240, "투명→흰 배경, 긴 변 1280", f"{w}x{h} {px}")
b2 = io.BytesIO()
Image.new("RGB", (80, 300)).save(b2, "PNG")
check(pi.to_jpeg(b2.getvalue()) is None and pi.to_jpeg(b"not an image") is None, "작거나 깨진 그림은 None")

# ── 6) 대안 → 제품 사진 고르기 ────────────────────────────────────────────────────
catalog = [
    {"id": 21, "name": "SLIM AMR", "family": "AMR"},
    {"id": 22, "name": "리프팅 AMR", "family": "AMR"},
    {"id": 136, "name": "포크리프트 AMR", "family": "AMR"},
    {"id": 117, "name": "2FINGER_GRIPPER", "family": "Magbot EOAT"},
    {"id": 225, "name": "마그네틱 그리퍼 MG series", "family": "Magbot EOAT"},
    {"id": 76, "name": "LYNX M20", "family": "4족 보행 로봇"},
]
check(pi.pick_cards("리프팅 AMR 이 선반 하부로 진입해 들어 올려 이송", "", catalog) == [22],
      "제품 이름이 그대로 나오면 그 제품", str(pi.pick_cards("리프팅 AMR 이 선반 하부로 진입해 들어 올려 이송", "", catalog)))
only_cat = pi.pick_cards("협동로봇이 적재한 박스를 AMR 로 창고까지 이송", "", catalog)
check(len(only_cat) == 1 and only_cat[0] in (21, 22, 136), "부류 말(AMR)만 나오면 AMR 한 장만", str(only_cat))
check(pi.pick_cards("자율주행 이동로봇에 포크 리프트 달아 팔레트 이송", "", catalog) == [136],
      "부류 말 + 이름 조각(포크·리프트) 겹치면 그 제품", str(pi.pick_cards("자율주행 이동로봇에 포크 리프트 달아 팔레트 이송", "", catalog)))
both = pi.pick_cards("SLIM AMR 이 부품을 가져오고 마그네틱 그리퍼 MG series 로 집는다", "", catalog)
check(both == [21, 225], "제품 둘이 나오면 둘 다(한도 2)", str(both))
check(pi.pick_cards("컨베이어 위 박스를 AMR 로 이송", "AMR 은 쓰지 않는다", catalog) == [],
      "넣지 않을 것에 걸린 부류는 뺌")
check(pi.pick_cards("사람이 수동으로 투입하는 컨베이어 라인", "", catalog) == [], "회사 제품이 안 나오면 없음")
check(pi.pick_cards("4족 보행 로봇으로 옥외 순찰", "", catalog) == [76], "4족 → LYNX")
pro_cat = catalog + [{"id": 77, "name": "LYNX M20 Pro", "family": "4족 보행 로봇"}]
check(pi.pick_cards("LYNX M20 Pro 로 옥외 순찰", "", pro_cat) == [77],
      "'M20 Pro' 가 나오면 M20 은 따로 안 붙임(같은 사진 두 장 방지)", str(pi.pick_cards("LYNX M20 Pro 로 옥외 순찰", "", pro_cat)))
check(pi.pick_cards("LYNX M20 으로 옥외 순찰", "", pro_cat) == [76], "M20 만 나오면 M20")
check(set(pi.SHARED_PHOTOS) == {"intro:product:lynxm20pro"},
      "M20 Pro 는 M20 과 같은 사진(X30 Pro 는 71쪽 자기 실물 그림)")
atc_cat = catalog + [{"id": 107, "name": "맥봇 자동툴체인져 / mTC시리즈", "family": "Magbot ATC"},
                     {"id": 147, "name": "맞춤형 관제 솔루션", "family": "관제 솔루션"}]
check(pi.pick_cards("batch 단위로 투입 후 검사", "", atc_cat) == [], "영문 부류 말은 단어 경계(batch 의 atc 아님)")
check(pi.pick_cards("로봇 툴체인저로 그리퍼를 바꿔 끼움", "", atc_cat)[:1] == [107], "툴체인저 → ATC 제품")
check(pi.pick_cards("관제 시스템으로 상태를 모니터링", "", atc_cat) == [], "관제(화면 캡처)는 부류 말로 안 고름")
check(pi.pick_cards("맞춤형 관제 솔루션 화면 연동", "", atc_cat) == [147], "이름이 나오면 관제도 고름")
check(pi.has_word("AMR로 이송", "amr") and not pi.has_word("Camry 부품", "amr"), "has_word: 한글 조사 붙어도 영문 단어 인식")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
