import { useEffect, useRef, useState } from "react";
import type { Challenge } from "../types";
import { MoreIcon, TrashIcon, PencilIcon } from "./icons";

interface Props {
  challenges: Challenge[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}

// 좌측 사이드바: 새 챌린지 + 현재 접속 중인 챌린지 목록 (메모리 전용)
export function Sidebar({
  challenges,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onRename,
}: Props) {
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const rootRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!menuFor) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setMenuFor(null);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuFor]);

  useEffect(() => {
    if (editingId) inputRef.current?.select();
  }, [editingId]);

  const startRename = (c: Challenge) => {
    setEditingId(c.id);
    setDraft(c.title);
    setMenuFor(null);
  };

  const commitRename = () => {
    if (editingId) onRename(editingId, draft);
    setEditingId(null);
  };

  return (
    <aside className="sidebar" ref={rootRef}>
      <div className="sidebar__brand">
        <div className="brand">
          <div className="brand__title">COSS X ANCHOR</div>
        </div>
      </div>

      <button className="sidebar__new" onClick={onNew}>
        + 새 챌린지
      </button>

      <div className="sidebar__section">챌린지</div>
      <nav className="sidebar__list">
        {challenges.map((c) => (
          <div
            key={c.id}
            className={"session-row" + (c.id === activeId ? " is-active" : "")}
          >
            {editingId === c.id ? (
              <input
                ref={inputRef}
                className="session-row__input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={commitRename}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename();
                  if (e.key === "Escape") setEditingId(null);
                }}
              />
            ) : (
              <button
                className="session-row__title"
                onClick={() => onSelect(c.id)}
                onDoubleClick={() => startRename(c)}
                title={c.title}
              >
                {c.title || "새 챌린지"}
              </button>
            )}

            <button
              className="session-row__more"
              aria-label="챌린지 메뉴"
              onClick={(e) => {
                e.stopPropagation();
                setMenuFor(menuFor === c.id ? null : c.id);
              }}
            >
              <MoreIcon />
            </button>

            {menuFor === c.id && (
              <div className="session-menu" role="menu">
                <button
                  className="session-menu__item"
                  role="menuitem"
                  onClick={() => startRename(c)}
                >
                  <PencilIcon />
                  이름 바꾸기
                </button>
                <button
                  className="session-menu__item is-danger"
                  role="menuitem"
                  onClick={() => {
                    onDelete(c.id);
                    setMenuFor(null);
                  }}
                >
                  <TrashIcon />
                  삭제
                </button>
              </div>
            )}
          </div>
        ))}
      </nav>
    </aside>
  );
}
