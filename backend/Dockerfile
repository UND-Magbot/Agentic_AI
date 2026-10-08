FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# draw.io 데스크톱 — 공정 개념도 PNG 내보내기(diagram/drawio.export_png).
# Electron 이라 화면이 필요해 xvfb 로 가상 디스플레이를 붙이고, 한글은 나눔고딕으로 렌더한다.
# libasound2t64: .deb 가 의존성으로 선언하지 않아 명시(Debian trixie 패키지명).
ARG DRAWIO_VERSION=31.5.3
RUN apt-get update \
 && curl -fsSL -o /tmp/drawio.deb \
      "https://github.com/jgraph/drawio-desktop/releases/download/v${DRAWIO_VERSION}/drawio-amd64-${DRAWIO_VERSION}.deb" \
 && apt-get install -y --no-install-recommends /tmp/drawio.deb xvfb xauth fonts-nanum libasound2t64 \
 && rm -f /tmp/drawio.deb \
 && rm -rf /var/lib/apt/lists/*

# LibreOffice Calc(헤드리스) — 견적서 엑셀을 같은 모양의 PDF 로(미리보기·PDF 내려받기, quote_xlsx.to_pdf).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-calc-nogui \
 && rm -rf /var/lib/apt/lists/*

# PDF 글꼴 — Calibri 와 크기가 같은 Carlito(열 너비 계산이 엑셀과 같게) + 맑은 고딕 → 나눔고딕 대체 규칙(fontconfig/).
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-crosextra-carlito \
 && rm -rf /var/lib/apt/lists/*
COPY fontconfig/99-und-office-fonts.conf /etc/fonts/conf.d/99-und-office-fonts.conf
RUN fc-cache -f

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd -m -u 1001 cortex \
 && chown -R cortex:cortex /app
USER cortex

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
