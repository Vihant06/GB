import {useState} from 'react';
import {MoreHorizontal, SquarePen, X} from 'lucide-react';
import type {SavedChat} from '../lib/chatHistory';

interface SavedChatsProps {
  chats: SavedChat[];
  activeChatId: string;
  loading: boolean;
  error: string | null;
  onOpen: (chat: SavedChat) => void;
  onClose: () => void;
  onNewChat: () => void;
  onRetry: () => void;
  onRename: (chat: SavedChat, title: string) => Promise<boolean>;
  onDelete: (chat: SavedChat) => Promise<boolean>;
}

export function SavedChats({chats, activeChatId, loading, error, onOpen, onClose, onNewChat, onRetry, onRename, onDelete}: SavedChatsProps) {
  const [action, setAction] = useState<{id: string; kind: 'menu' | 'rename' | 'delete'} | null>(null);
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(chat: SavedChat, kind: 'rename' | 'delete') {
    if (busy || (kind === 'rename' && !title.trim())) return;
    setBusy(true);
    try {
      const success = kind === 'rename' ? await onRename(chat, title.trim()) : await onDelete(chat);
      if (success) setAction(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" aria-label="Dismiss chat history" onClick={onClose}
        className="fixed inset-0 z-30 bg-black/35 sm:hidden" />
      <aside id="chat-history" aria-labelledby="chat-history-title"
        className="fixed inset-y-3 left-3 z-40 flex w-[min(18rem,calc(100vw-1.5rem))] min-h-0 shrink-0 flex-col rounded-[24px] border-2 border-[#111111] bg-white p-3 text-sm text-[#111111] dark:border-[#42413A] dark:bg-[#20201C] dark:text-[#ECEBE4] sm:static sm:z-auto sm:w-60 lg:w-64">
        <div className="flex items-center justify-between gap-2 px-2 py-1">
          <h2 id="chat-history-title" className="font-semibold">Chat history</h2>
          <button type="button" onClick={onClose} aria-label="Collapse chat history" className="rounded-lg p-1.5 hover:bg-neutral-100 dark:hover:bg-neutral-800">
            <X size={17} aria-hidden="true" />
          </button>
        </div>
        <button type="button" onClick={onNewChat}
          className="my-2 inline-flex items-center gap-2 self-start rounded-xl border border-[#111111] bg-[#F7EA78] px-3 py-2 font-medium text-[#111111]">
          <SquarePen size={14} aria-hidden="true" /> New chat
        </button>
        {loading && <p role="status" className="px-2 py-1 text-xs opacity-75">Loading chats…</p>}
        {error && (
          <div className="mx-1 mb-2 rounded-xl border border-red-300 p-2.5 text-xs dark:border-red-800">
            <p role="alert">{error}</p>
            <button type="button" onClick={onRetry} disabled={loading} className="mt-2 underline disabled:opacity-60">Retry history</button>
          </div>
        )}
        {!chats.length && !loading && !error && (
          <p className="px-2 py-2 text-xs opacity-75">No saved chats yet. Send a message to start one.</p>
        )}
        {chats.length > 0 && (
          <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto">
            {chats.map(chat => {
              const kind = action?.id === chat.id ? action.kind : null;
              return (
              <li key={chat.id} className={`rounded-xl ${chat.id === activeChatId ? 'bg-[#E3F4FE] dark:bg-[#243644]' : ''}`}>
                <div className="flex items-center">
                <button type="button" onClick={() => onOpen(chat)}
                  aria-current={chat.id === activeChatId ? 'true' : undefined}
                  className={`min-w-0 flex-1 rounded-xl px-2.5 py-2 text-left text-xs hover:bg-neutral-100 dark:hover:bg-neutral-800 ${chat.id === activeChatId ? 'font-semibold' : ''}`}>
                  <span className="block truncate">{chat.title}</span>
                  {chat.repo_name && <span className="block truncate text-[11px] opacity-75">{chat.repo_name}</span>}
                </button>
                <button type="button" disabled={busy} aria-label={`Actions for ${chat.title}`}
                  aria-expanded={kind !== null}
                  onClick={() => setAction(kind ? null : {id: chat.id, kind: 'menu'})}
                  className="mr-1 shrink-0 rounded-lg p-2 hover:bg-neutral-100 dark:hover:bg-neutral-800">
                  <MoreHorizontal size={16} aria-hidden="true" />
                </button>
                </div>
                {kind === 'menu' && (
                  <div className="flex gap-3 px-2.5 pb-2 text-xs">
                    <button type="button" className="underline" onClick={() => { setTitle(chat.title); setAction({id: chat.id, kind: 'rename'}); }}>Rename</button>
                    <button type="button" className="text-red-700 underline dark:text-red-300" onClick={() => setAction({id: chat.id, kind: 'delete'})}>Delete</button>
                  </div>
                )}
                {kind === 'rename' && (
                  <form className="space-y-2 px-2.5 pb-2 text-xs" onSubmit={event => { event.preventDefault(); void submit(chat, 'rename'); }}>
                    <label className="block">Chat name
                      <input autoFocus required maxLength={90} value={title} disabled={busy} onChange={event => setTitle(event.target.value)}
                        onKeyDown={event => { if (event.key === 'Escape' && !busy) setAction(null); }}
                        className="mt-1 w-full rounded-lg border border-neutral-500 bg-transparent px-2 py-1" />
                    </label>
                    <div className="flex gap-3">
                      <button type="submit" disabled={busy || !title.trim()} className="font-semibold underline disabled:opacity-50">Save</button>
                      <button type="button" disabled={busy} onClick={() => setAction(null)}>Cancel</button>
                    </div>
                  </form>
                )}
                {kind === 'delete' && (
                  <div className="space-y-2 px-2.5 pb-2 text-xs">
                    <p>Delete this chat and its messages?</p>
                    <div className="flex gap-3">
                      <button type="button" disabled={busy} onClick={() => void submit(chat, 'delete')}
                        className="font-semibold text-red-700 underline disabled:opacity-50 dark:text-red-300">Delete chat</button>
                      <button type="button" disabled={busy} onClick={() => setAction(null)}>Cancel</button>
                    </div>
                  </div>
                )}
              </li>
              );
            })}
          </ul>
        )}
      </aside>
    </>
  );
}
