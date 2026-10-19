import receiptCss from "../styles/receipt-print.css?inline";

export function receiptPageSize(widthMm: number, heightPx: number): string {
  if (![58, 80].includes(widthMm) || !Number.isFinite(heightPx) || heightPx <= 0) {
    throw new Error("영수증 용지 크기를 확인해 주세요.");
  }
  // CSS layout uses 96 pixels per inch; round upward and allow 1mm for rounding.
  const heightMm = Math.ceil(heightPx * 25.4 / 96) + 1;
  if (heightMm > 2000) throw new Error("영수증이 너무 깁니다. 내용을 확인해 주세요.");
  return `${widthMm}mm ${heightMm}mm`;
}

/** Open the native print dialog with only this receipt on a content-sized roll page. */
export async function printReceipt(source: HTMLElement, widthMm: number, contentWidthMm: number) {
  if (!Number.isFinite(contentWidthMm) || contentWidthMm <= 0 || contentWidthMm > widthMm - 2) {
    throw new Error("프린터의 실제 인쇄 폭을 확인해 주세요.");
  }
  const frame = document.createElement("iframe");
  frame.title = "영수증 인쇄";
  frame.setAttribute("aria-hidden", "true");
  Object.assign(frame.style, { position: "fixed", left: "-10000px", top: "0", width: `${widthMm}mm`, height: "1px", border: "0" });
  document.body.append(frame);
  const cleanup = () => frame.remove();
  try {
    const doc = frame.contentDocument!;
    doc.open(); doc.write("<!doctype html><html lang='ko'><head><title>AI SLOP STOP 영수증</title></head><body></body></html>"); doc.close();
    const style = doc.createElement("style"); style.textContent = receiptCss; doc.head.append(style);
    const copy = source.cloneNode(true) as HTMLElement;
    copy.querySelectorAll(".receipt-actions, .receipt-photo-controls, .receipt-preview-status, .receipt-print-error").forEach(el => el.remove());
    copy.style.setProperty("--paper-width", `${widthMm}mm`);
    copy.style.setProperty("--side-padding", `${(widthMm - contentWidthMm) / 2}mm`);
    doc.body.append(copy);
    await doc.fonts.ready;
    await Promise.all(Array.from(copy.querySelectorAll("img")).map(img => img.decode()));
    const page = doc.createElement("style");
    page.textContent = `@page { size: ${receiptPageSize(widthMm, copy.getBoundingClientRect().height)}; margin: 0; }`;
    doc.head.append(page);
    // Retain the document while the native dialog is open; release after printing/cancel.
    frame.contentWindow!.addEventListener("afterprint", cleanup, { once: true });
    setTimeout(cleanup, 600_000);
    frame.contentWindow!.focus(); frame.contentWindow!.print();
  } catch (e) {
    cleanup();
    throw e;
  }
}
