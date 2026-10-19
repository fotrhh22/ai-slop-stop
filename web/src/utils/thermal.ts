export type ThermalMethod = "atkinson" | "floyd-steinberg" | "mean";
export interface ThermalOptions {
  widthDots: number;
  method: ThermalMethod;
  brightness: number;
  contrast: number;
}

// Brightness is conventional luminance: 0 = black, 255 = white.
// Each algorithm starts from the original pixels; methods are never stacked.
export function monochromePixels(
  rgba: Uint8ClampedArray, width: number, height: number,
  { method, brightness, contrast }: Omit<ThermalOptions, "widthDots">,
): Uint8ClampedArray {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1
      || width * height > 7_200_000 || rgba.length !== width * height * 4) throw new Error("INVALID_IMAGE_SIZE");
  if (!["atkinson", "floyd-steinberg", "mean"].includes(method)
      || !Number.isFinite(brightness) || brightness < 0.5 || brightness > 1.5
      || !Number.isFinite(contrast) || contrast < 0.5 || contrast > 2) throw new Error("INVALID_THERMAL_OPTIONS");
  const gray = new Float32Array(width * height);
  let sum = 0, min = 255, max = 0;
  for (let i = 0; i < gray.length; i++) {
    const j = i * 4, alpha = rgba[j + 3] / 255;
    const luma = (0.2126 * rgba[j] + 0.7152 * rgba[j + 1] + 0.0722 * rgba[j + 2]) * alpha + 255 * (1 - alpha);
    const value = Math.max(0, Math.min(255, ((luma - 127.5) * contrast + 127.5) * brightness));
    gray[i] = value;
    sum += value; min = Math.min(min, value); max = Math.max(max, value);
  }
  // Constant white/black images must remain white/black in mean-threshold mode.
  const threshold = method === "mean" && min !== max ? sum / gray.length : 127.5;
  const output = new Uint8ClampedArray(rgba.length);
  // [dx, dy, weight]. Atkinson intentionally diffuses only 6/8 of the error.
  const taps = method === "atkinson"
    ? [[1, 0, 1 / 8], [2, 0, 1 / 8], [-1, 1, 1 / 8], [0, 1, 1 / 8], [1, 1, 1 / 8], [0, 2, 1 / 8]]
    : [[1, 0, 7 / 16], [-1, 1, 3 / 16], [0, 1, 5 / 16], [1, 1, 1 / 16]];
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = y * width + x, value = gray[i] >= threshold ? 255 : 0;
      output[i * 4] = output[i * 4 + 1] = output[i * 4 + 2] = value;
      output[i * 4 + 3] = 255;
      if (method === "mean") continue;
      const error = gray[i] - value;
      for (const [dx, dy, weight] of taps) {
        const nx = x + dx, ny = y + dy;
        if (nx >= 0 && nx < width && ny < height) gray[ny * width + nx] += error * weight;
      }
    }
  }
  return output;
}

export async function thermalPng(source: Blob, options: ThermalOptions): Promise<Blob> {
  if (!Number.isInteger(options.widthDots) || options.widthDots < 128 || options.widthDots > 1200
      || options.widthDots % 8 !== 0) throw new Error("인쇄 폭은 128~1200 사이의 8배수로 입력해 주세요.");
  const image = await createImageBitmap(source);
  try {
    const height = Math.max(1, Math.round(image.height * options.widthDots / image.width));
    if (height > 6000) throw new Error("출력 이미지가 너무 깁니다. 인쇄 폭을 줄여 주세요.");
    const canvas = document.createElement("canvas");
    canvas.width = options.widthDots; canvas.height = height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("이 브라우저에서 열전사 미리보기를 만들 수 없습니다.");
    ctx.fillStyle = "white"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
    // Resize before dithering. Do not resample the resulting dot pattern.
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
    pixels.data.set(monochromePixels(pixels.data, canvas.width, canvas.height, options));
    ctx.putImageData(pixels, 0, 0);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => {
      if (blob) resolve(blob); else reject(new Error("흑백 PNG를 만들지 못했습니다."));
    }, "image/png"));
  } finally { image.close(); }
}
