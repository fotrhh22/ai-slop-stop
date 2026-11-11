export interface ChallengeTarget {
  id: string;
  theme: "A" | "B" | "C";
  themeLabel: string;
  difficulty: "하" | "중" | "상";
  title: string;
  imageUrl: string;
  hints: { label: string; keywords: string[] }[];
}

const target = (
  id: string, theme: ChallengeTarget["theme"], difficulty: ChallengeTarget["difficulty"],
  title: string, subject: string[], scene: string[],
): ChallengeTarget => ({
  id, theme, difficulty, title,
  themeLabel: { A: "다람쥐의 컵", B: "여우의 정원", C: "고양이의 도넛" }[theme],
  imageUrl: `${import.meta.env.BASE_URL}challenge-targets/${id.toLowerCase()}.png`,
  hints: [
    { label: "그림체", keywords: [{ A: "픽셀아트", B: "따뜻한 2D 동화책 그림", C: "부드러운 3D" }[theme]] },
    { label: "캐릭터·물건·행동", keywords: subject },
    { label: "배경·구도", keywords: scene },
  ],
});

export const CHALLENGE_TARGETS: ChallengeTarget[] = [
  target("A-LOW", "A", "하", "컵을 든 다람쥐", ["주황 아기 다람쥐", "초록 뚜껑의 크림색 컵", "컵 들기"], ["정면", "단색 크림색 배경"]),
  target("A-MID", "A", "중", "숲에서 한 모금", ["주황 아기 다람쥐", "초록 뚜껑의 크림색 컵", "컵을 입에 대고 마시기"], ["가을 숲", "나무다리"]),
  target("A-HIGH", "A", "상", "토끼에게 컵 건네기", ["주황 아기 다람쥐", "흰 토끼", "초록 뚜껑의 크림색 컵", "컵을 건네고 받기"], ["왼쪽 다람쥐·오른쪽 토끼", "가을 숲", "나무다리"]),
  target("B-LOW", "B", "하", "물뿌리개를 든 여우", ["주황 아기 여우", "은색 물뿌리개", "물뿌리개 들기"], ["정면", "단색 크림색 배경"]),
  target("B-MID", "B", "중", "새싹에 물 주기", ["주황 아기 여우 한 마리", "은색 물뿌리개", "물뿌리개를 기울여 새싹에 물 주기"], ["옥상 텃밭", "도시", "노을"]),
  target("B-HIGH", "B", "상", "함께 가꾸는 정원", ["주황 아기 여우", "흰 토끼", "여우는 은색 물뿌리개로 물 주기", "토끼는 작은 삽으로 흙 파기"], ["왼쪽 여우·오른쪽 토끼", "옥상 텃밭", "도시와 노을"]),
  target("C-LOW", "C", "하", "도넛을 든 고양이", ["회색·흰색 아기 고양이", "분홍색 도넛", "앉아 있기"], ["정면", "단색 연분홍 배경"]),
  target("C-MID", "C", "중", "도넛과 하늘 여행", ["회색·흰색 아기 고양이", "분홍색 도넛", "몸을 수평으로 뻗어 날아가기"], ["오른쪽으로 비행", "파란 하늘", "구름", "무지개"]),
  target("C-HIGH", "C", "상", "하늘에서 만난 토끼", ["회색·흰색 아기 고양이", "분홍색 도넛", "흰 토끼", "공중에서 서로 마주 보기"], ["왼쪽 고양이·오른쪽 토끼", "수평 비행", "파란 하늘과 구름", "무지개"]),
];

// Use the existing image-upload API so the selected target is persisted in Storage.
export async function loadTargetDataUrl(target: ChallengeTarget): Promise<string> {
  const response = await fetch(target.imageUrl);
  if (!response.ok) throw new Error("목표 이미지를 불러오지 못했습니다. 다시 선택해 주세요.");
  const blob = await response.blob();
  if (!blob.type.startsWith("image/")) throw new Error("목표 이미지 파일을 확인하지 못했습니다.");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("목표 이미지를 읽지 못했습니다."));
    reader.onerror = () => reject(new Error("목표 이미지를 읽지 못했습니다."));
    reader.readAsDataURL(blob);
  });
}
