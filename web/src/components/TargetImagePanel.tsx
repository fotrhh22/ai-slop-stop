import { CHALLENGE_TARGETS } from "../data/challengeTargets";

interface Props {
  targetImageUrl: string;
  targetId?: string;
  isCustom: boolean;
  onUpload: (dataUrl: string) => void;
}

// 상단 고정 패널: 이 챌린지의 목표 이미지(고정) 미리보기.
// 목표 이미지는 챌린지마다 고정이므로, 바꾸려면 새 챌린지를 시작한다.
export function TargetImagePanel({ targetImageUrl, targetId, onUpload }: Props) {
  const target = CHALLENGE_TARGETS.find(t => t.id === targetId);
  return (
    <div className="target-panel">
      <div className="target-panel__thumb">
        <img src={targetImageUrl} alt="목표 이미지" />
      </div>
      <div className="target-panel__body">
        <div className="target-panel__label">{target ? `${target.id} · ${target.title}` : "목표 이미지"}</div>
        <div className="target-panel__hint">
          이 이미지에 최대한 가까운 결과가 나오도록 프롬프트를 작성하세요.
          목표를 바꾸려면 새 챌린지를 시작하세요.
        </div>
        {target && <details className="target-hints" key={target.id}>
          <summary>힌트 키워드 보기</summary>
          {target.hints.map(group => <div key={group.label} className="target-hints__group">
            <strong>{group.label}</strong>
            <div>{group.keywords.map(word => <span key={word}>{word}</span>)}</div>
          </div>)}
        </details>}
        <button className="btn-secondary" onClick={() => onUpload("")}>
          다른 목표로 새 챌린지
        </button>
      </div>
    </div>
  );
}
