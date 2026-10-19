import { useRef, useState } from "react";
import { SendIcon } from "./icons";

interface Props {
  disabled?: boolean;
  onSend: (text: string) => void;
}

// 하단 입력창: Enter 전송, Shift+Enter 줄바꿈, 높이 자동
export function Composer({ disabled, onSend }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const send = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(text);
    setText("");
    if (ref.current) ref.current.style.height = "auto";
  };

  return (
    <div className="composer">
      <div className="composer__inner">
        <textarea
          ref={ref}
          value={text}
          placeholder="이미지를 설명하는 프롬프트를 입력하세요"
          rows={1}
          onChange={(e) => {
            setText(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = e.target.scrollHeight + "px";
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          className="composer__send"
          onClick={send}
          disabled={disabled || !text.trim()}
          aria-label="전송"
        >
          <SendIcon />
        </button>
      </div>
    </div>
  );
}
