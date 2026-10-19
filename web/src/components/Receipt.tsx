import { useEffect, useRef, useState, type CSSProperties } from "react";
import { fetchReceiptImage } from "../api/client";
import { thermalPng, type ThermalMethod, type ThermalOptions } from "../utils/thermal";
import { printReceipt } from "../utils/printReceipt";
import type { Receipt as ReceiptData } from "../types";

export function Receipt({ receipt: r, accessToken, onRefresh }: {
  receipt: ReceiptData; accessToken: string; onRefresh: () => Promise<void>;
}) {
  const [view, setView] = useState<"color" | "thermal">("color");
  const [paperWidth, setPaperWidth] = useState(80);
  const [dpi, setDpi] = useState(203);
  const [options, setOptions] = useState<ThermalOptions>({ widthDots: 576, method: "atkinson", brightness: 1, contrast: 1 });
  const [retry, setRetry] = useState(0);
  const [preview, setPreview] = useState<{ key: string; url?: string; error?: string }>({ key: "" });
  const [loadedUrl, setLoadedUrl] = useState("");
  const [colorError, setColorError] = useState(false);
  const paper = useRef<HTMLElement>(null);
  const [printing, setPrinting] = useState(false);
  const [printError, setPrintError] = useState("");
  const original = useRef<{ id: string; blob: Blob }>();
  const imageKey = JSON.stringify([r.id, options]);
  const photoWidthMm = options.widthDots * 25.4 / dpi;
  const settingsError = !Number.isInteger(options.widthDots) || options.widthDots < 128 || options.widthDots > 1200 || options.widthDots % 8 !== 0
    ? "인쇄 폭은 128~1200 사이의 8배수로 입력해 주세요."
    : photoWidthMm > paperWidth - 2 ? "선택한 용지에 비해 인쇄 폭이 큽니다. 도트 폭 또는 해상도를 확인해 주세요." : "";

  useEffect(() => {
    if (view !== "thermal" || settingsError) return;
    let active = true, objectUrl: string | undefined;
    const controller = new AbortController();
    setPreview({ key: imageKey });
    const timer = setTimeout(async () => {
      try {
        const blob = original.current?.id === r.id ? original.current.blob
          : await fetchReceiptImage(r, accessToken, controller.signal);
        if (!active) return;
        original.current = { id: r.id, blob };
        const monochrome = await thermalPng(blob, options);
        if (!active) return;
        objectUrl = URL.createObjectURL(monochrome);
        setPreview({ key: imageKey, url: objectUrl });
      } catch (e) {
        if (active) setPreview({ key: imageKey, error: e instanceof Error ? e.message : "열전사 미리보기 생성 실패" });
      }
    }, 250);
    return () => {
      active = false; clearTimeout(timer); controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [view, imageKey, accessToken, settingsError, retry]);

  const current = preview.key === imageKey ? preview : undefined;
  const imageUrl = view === "color" ? r.image_url : !settingsError && !current?.error ? current?.url : undefined;
  const imageReady = !!imageUrl && loadedUrl === imageUrl;
  const printStyle = {
    "--thermal-paper-width": `${paperWidth}mm`,
    "--thermal-photo-width": `${photoWidthMm}mm`,
    "--thermal-side-padding": `${Math.max(0, (paperWidth - photoWidthMm) / 2)}mm`,
  } as CSSProperties;
  const openPrint = async () => {
    if (!paper.current) return;
    setPrinting(true); setPrintError("");
    try { await printReceipt(paper.current, paperWidth, photoWidthMm); }
    catch { setPrintError("인쇄 준비에 실패했습니다. 이미지와 용지 설정을 확인하고 다시 시도해 주세요."); }
    finally { setPrinting(false); }
  };
  return <article ref={paper} className="receipt-paper" aria-label="성공 영수증" data-print-mode={view} style={printStyle}>
    <div className="receipt-actions">
      <button disabled={!imageReady || !!settingsError || printing} onClick={openPrint}>{view === "thermal" ? "열전사 인쇄 / PDF 저장" : "인쇄 / PDF 저장"}</button>
      <button onClick={onRefresh}>새로고침</button>
    </div>
    {printError && <p className="receipt-print-error" role="alert">{printError}</p>}
    <p className="receipt-eyebrow">AI SLOP STOP CHALLENGE</p>
    <h1>PASS 영수증</h1>
    {r.source === "mock" ? <p className="receipt-warning">샘플 영수증</p>
      : !r.verified && <p className="receipt-warning">기록 확인 필요</p>}
    <dl>
      <dt>성공 시각</dt><dd>{new Date(r.created_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}</dd>
      <dt>승인 방식</dt><dd>{r.pass_source === "staff" ? "STAFF 수동 승인" : "AI 판정"}</dd>
    </dl>
    <div className="receipt-photo-controls">
      <div className="receipt-photo-tabs" role="group" aria-label="영수증 사진 표시 방식">
        <button aria-pressed={view === "color"} onClick={() => setView("color")}>컬러 원본</button>
        <button aria-pressed={view === "thermal"} onClick={() => setView("thermal")}>열전사 미리보기</button>
      </div>
      <div className="receipt-thermal-settings">
          <label>용지 폭<select value={paperWidth} onChange={e => {
            const width = Number(e.target.value); setPaperWidth(width);
            setDpi(203); setOptions(o => ({ ...o, widthDots: width === 58 ? 384 : 576 }));
          }}><option value={80}>80 mm</option><option value={58}>58 mm</option></select></label>
          <label>인쇄 폭 (dots)<input type="number" min={128} max={1200} step={8} value={options.widthDots}
            onChange={e => setOptions(o => ({ ...o, widthDots: Number(e.target.value) }))} /></label>
          <label>해상도<select value={dpi} onChange={e => setDpi(Number(e.target.value))}>
            <option value={203}>203 dpi</option><option value={300}>300 dpi</option>
          </select></label>
      </div>
      <p className="receipt-note">{paperWidth}mm 용지 · 내용 길이에 맞춘 PDF. 인쇄창에서 같은 용지와 배율 100%를 선택하고 머리글·바닥글을 꺼 주세요.</p>
      {view === "thermal" && <>
        <label>흑백 변환 방식
          <select value={options.method} onChange={e => setOptions(o => ({ ...o, method: e.target.value as ThermalMethod }))}>
            <option value="atkinson">Atkinson · 기본</option>
            <option value="floyd-steinberg">Floyd–Steinberg · 오류 확산</option>
            <option value="ordered-bayer">Ordered Bayer · 규칙 패턴</option>
            <option value="ordered-blue-noise">Ordered Blue Noise · 미세 입자</option>
          </select>
        </label>
        <div className="receipt-thermal-settings">
          <label>밝기 {options.brightness.toFixed(2)}<input type="range" min={0.5} max={1.5} step={0.05} value={options.brightness}
            onChange={e => setOptions(o => ({ ...o, brightness: Number(e.target.value) }))} /></label>
          <label>대비 {options.contrast.toFixed(2)}<input type="range" min={0.5} max={2} step={0.05} value={options.contrast}
            onChange={e => setOptions(o => ({ ...o, contrast: Number(e.target.value) }))} /></label>
        </div>
        <p className="receipt-note">프린터의 실제 인쇄 폭·해상도에 맞추고 인쇄 배율은 100%로 설정해 주세요. 밝은 픽셀은 흰색으로 표현합니다.</p>
        {current?.url && !settingsError && <a className="receipt-download" href={current.url}
          download={`thermal-receipt-${options.method}-${options.widthDots}px.png`}>흑백 PNG 저장</a>}
      </>}
    </div>
    {imageUrl ? <img className={"receipt-image" + (view === "thermal" ? " receipt-image--thermal" : "")} src={imageUrl}
      alt={view === "thermal" ? `${options.method} 방식의 열전사 출력 미리보기` : "PASS 받은 생성 이미지"}
      onLoad={() => { setLoadedUrl(imageUrl); setColorError(false); }}
      onError={() => {
        setLoadedUrl("");
        if (view === "thermal") setPreview({ key: imageKey, error: "흑백 이미지를 표시하지 못했습니다." });
        else setColorError(true);
      }} /> : null}
    {view === "color" && colorError && <p className="receipt-preview-status" role="alert">원본 이미지를 불러오지 못했습니다. 새로고침해 주세요.</p>}
    {view === "thermal" && <div className="receipt-preview-status" aria-live="polite">
      {settingsError || current?.error ? <p role="alert">{settingsError || current?.error}
        {current?.error && <button onClick={() => setRetry(n => n + 1)}>다시 만들기</button>}</p>
        : !imageUrl ? <p role="status">열전사 이미지 준비 중…</p> : <p>{options.widthDots} dots · {dpi} dpi · {options.method}</p>}
    </div>}
    <h2>성공 프롬프트</h2>
    <pre>{r.prompt}</pre>
    {r.pass_source === "router" && r.router_reason && <>
      <h2>통과 이유</h2>
      <p className="receipt-reason">{r.router_reason}</p>
    </>}
    <h2>성공까지 누적 사용량</h2>
    <dl>
      <dt>이미지 생성 요청</dt><dd>{r.generation_calls.toLocaleString("ko-KR")}회</dd>
      <dt>평균 프롬프트 글자수<br /><small>공백 포함 · 모든 생성 시도</small></dt>
      <dd>{r.average_prompt_length == null ? "—" : `${r.average_prompt_length.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}자`}</dd>
      <dt>판정 요청</dt><dd>{r.router_calls.toLocaleString("ko-KR")}회</dd>
      <dt>{r.usage_complete ? "총 사용 토큰" : "확인된 사용 토큰"}</dt>
      <dd>{r.known_tokens_total.toLocaleString("ko-KR")}</dd>
    </dl>
    {Object.keys(r.tokens_by_model).length > 0 && <>
      <h2>모델별 사용 토큰</h2>
      {Object.entries(r.tokens_by_model)
        .sort(([a, au], [b, bu]) => Number(bu.generation_calls > 0) - Number(au.generation_calls > 0) || a.localeCompare(b))
        .map(([model, usage]) => <dl className="receipt-token-model" key={model}>
          <dt>
            {usage.generation_calls > 0 ? (usage.router_calls > 0 ? "생성·판정" : "이미지 생성") : "AI 판정"}
            <span className="receipt-model-name">{model.replace(/^[^/]+\//, "")}</span>
          </dt>
          <dd>{typeof usage.known_total_tokens === "number" ? usage.known_total_tokens.toLocaleString("ko-KR") : "미확인"}
            {usage.calls_without_total_tokens > 0 && <span className="receipt-model-name">확인된 합계</span>}
          </dd>
        </dl>)}
    </>}
    {!r.usage_complete && <p className="receipt-note">일부 사용량 미확인</p>}
  </article>;
}
