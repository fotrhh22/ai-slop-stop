import { useRef, useState } from "react";

interface Props {
  onCancel: () => void;
  onConfirm: (dataUrl: string) => void;
}

// 새 챌린지 시작 전, 목표 이미지를 반드시 먼저 올리게 하는 모달.
// 이미지를 고르기 전에는 "시작" 버튼이 비활성화된다.
export function TargetImagePicker({ onCancel, onConfirm }: Props) {
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const pick = () => fileRef.current?.click();

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("이미지 파일만 올릴 수 있습니다.");
      return;
    }
    setError(null);
    const reader = new FileReader();
    reader.onload = () => setPreview(String(reader.result));
    reader.onerror = () => setError("이미지를 읽지 못했습니다.");
    reader.readAsDataURL(file);
    e.target.value = "";
  };

  return (
    <div className="modal-scrim" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal__title">목표 이미지 설정</h2>
        <p className="modal__desc">
          이 챌린지에서 재현할 목표 이미지를 먼저 올려주세요. 목표 이미지를
          설정해야 챌린지가 시작됩니다.
        </p>

        <div
          className={"dropzone" + (preview ? " has-image" : "")}
          onClick={pick}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && pick()}
        >
          {preview ? (
            <img src={preview} alt="목표 이미지 미리보기" />
          ) : (
            <div className="dropzone__hint">
              클릭해서 목표 이미지 선택
              <span>PNG · JPG · WEBP</span>
            </div>
          )}
        </div>

        {error && <div className="modal__error">{error}</div>}

        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          style={{ display: "none" }}
          onChange={onFile}
        />

        <div className="modal__actions">
          <button className="btn-secondary" onClick={onCancel}>
            취소
          </button>
          {preview && (
            <button className="btn-secondary" onClick={pick}>
              다시 선택
            </button>
          )}
          <button
            className="btn-primary"
            disabled={!preview}
            onClick={() => preview && onConfirm(preview)}
          >
            이 이미지로 챌린지 시작
          </button>
        </div>
      </div>
    </div>
  );
}
