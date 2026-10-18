interface Props {
  targetImageUrl: string;
  isCustom: boolean;
  onUpload: (dataUrl: string) => void;
}

// 상단 고정 패널: 이 챌린지의 목표 이미지(고정) 미리보기.
// 목표 이미지는 챌린지마다 고정이므로, 바꾸려면 새 챌린지를 시작한다.
export function TargetImagePanel({ targetImageUrl, onUpload }: Props) {
  return (
    <div className="target-panel">
      <div className="target-panel__thumb">
        <img src={targetImageUrl} alt="목표 이미지" />
      </div>
      <div className="target-panel__body">
        <div className="target-panel__label">목표 이미지</div>
        <div className="target-panel__hint">
          이 이미지에 최대한 가까운 결과가 나오도록 프롬프트를 작성하세요.
          목표를 바꾸려면 새 챌린지를 시작하세요.
        </div>
        <button className="btn-secondary" onClick={() => onUpload("")}>
          다른 목표로 새 챌린지
        </button>
      </div>
    </div>
  );
}
