import {useEffect, useRef, useState} from 'react';
import {
  AlertCircle,
  ExternalLink,
  FileText,
  Github,
  GitBranch,
  Mic,
  MoreHorizontal,
  Paperclip,
  RefreshCw,
  SendHorizontal,
  Sparkles,
  SquarePen,
  Trash2,
  X,
} from 'lucide-react';
import {AiStatus} from '@/components/ui/ai-status';
import {StreamingResponse} from '@/components/ui/streaming-response';
import {WebSearch, type WebSource} from '@/components/ui/web-search';
import {SAMPLE_ATTACHMENTS} from '../data/chatData';
import {ChatMessage, ChatSession, RepoTarget} from '../types/chat';

function getAiStatusState(step?: string): 'thinking' | 'researching' | 'analyzing' | 'writing' {
  if (!step) return 'thinking';
  const lower = step.toLowerCase();
  if (lower.includes('plan') || lower.includes('orchestrat')) return 'thinking';
  if (lower.startsWith('looking')) return 'researching';
  if (lower.startsWith('checking')) return 'analyzing';
  if (lower.includes('synthes') || lower.includes('token') || lower.includes('report') || lower.includes('writ')) return 'writing';
  return 'thinking';
}

function getSourcesForMessage(message: ChatMessage, repo?: RepoTarget | null): WebSource[] {
  const sources: WebSource[] = [];
  const seenIds = new Set<string>();

  // 1. Extract verified file citations from message text (e.g. [app/main.py:L14-L45] or [file:app/main.py:L14-L45])
  if (message.text) {
    const citationRegex = /\[(?:file:)?([^:\]\s]+):L(\d+)(?:-L(\d+))?\]/g;
    const verifiedCitations = new Set(message.verifiedCitations ?? []);
    let match;
    while ((match = citationRegex.exec(message.text)) !== null) {
      const rawPath = match[1];
      const startLine = match[2];
      const endLine = match[3];
      const citation = `${rawPath}:L${startLine}-L${endLine ?? startLine}`;
      if (
        rawPath &&
        verifiedCitations.has(citation) &&
        !rawPath.startsWith('http') &&
        !rawPath.includes(' ') &&
        !seenIds.has(citation)
      ) {
        seenIds.add(citation);
        const lineFragment = startLine ? (endLine ? `#L${startLine}-L${endLine}` : `#L${startLine}`) : '';
        const lineLabel = startLine ? (endLine ? `:L${startLine}-L${endLine}` : `:L${startLine}`) : '';
        const url = repo ? `${repo.url}/blob/main/${rawPath}${lineFragment}` : `#${rawPath}`;
        sources.push({
          id: `citation-${citation}`,
          title: `${rawPath}${lineLabel}`,
          url,
          snippet: `Verified line-level codebase citation from ${rawPath}`,
        });
      }
    }
  }


  return sources;
}

interface ChatSessionPaneProps {
  session: ChatSession;
  activeRepo?: RepoTarget | null;
  isTyping: boolean;
  onSendMessage: (
    text: string,
    attachment?: {fileName: string; fileSize: string; content?: string}
  ) => void;
  onRetryLast: () => void;
  onNewChat: () => void;
  onClearConversation: () => void;
  onOpenRepoModal?: () => void;
}

export function ChatSessionPane({
  session,
  activeRepo,
  isTyping,
  onSendMessage,
  onRetryLast,
  onNewChat,
  onClearConversation,
  onOpenRepoModal,
}: ChatSessionPaneProps) {
  const [draft, setDraft] = useState('');
  const [pendingAttachment, setPendingAttachment] = useState<{
    fileName: string;
    fileSize: string;
    content?: string;
  } | null>(null);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showHeaderMenu, setShowHeaderMenu] = useState(false);

  const effectiveRepo = session.repoTarget || activeRepo;

  const streamEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    streamEndRef.current?.scrollIntoView({behavior: 'smooth'});
  }, [session.messages.length, session.messages[session.messages.length - 1]?.text, isTyping]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isTyping) {
      return;
    }
    const trimmed = draft.trim();
    if (!trimmed && !pendingAttachment) {
      return;
    }
    const textToSend =
      trimmed || `Please review the attached file: ${pendingAttachment?.fileName}`;
    onSendMessage(textToSend, pendingAttachment ?? undefined);
    setDraft('');
    setPendingAttachment(null);
    inputRef.current?.focus();
  };

  const handleVoiceShortcut = () => {
    if (isTyping) {
      return;
    }
    onSendMessage('Can you give me a quick summary of what we discussed?');
  };

  const handleFilePicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) {
      return;
    }
    const sizeKb =
      file.size >= 1024
        ? `${(file.size / 1024).toFixed(2)}KB`
        : `${file.size}B`;
    const reader = new FileReader();
    reader.onload = () => {
      const content =
        typeof reader.result === 'string'
          ? reader.result.slice(0, 12000)
          : undefined;
      setPendingAttachment({
        fileName: file.name,
        fileSize: sizeKb,
        content,
      });
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  return (
    <section
      aria-label={`Chat session with ${session.name}`}
      className="flex flex-1 flex-col bg-white dark:bg-[#20201C] h-full min-w-0 overflow-hidden transition-colors duration-150"
    >
      {/* Active Chat Header */}
      <header className="px-4 sm:px-6 py-3.5 border-b-[1.75px] border-[#111111] dark:border-[#42413A] flex items-center justify-between gap-4 shrink-0 relative">
        <div className="flex items-center gap-3 min-w-0">
          <div className="relative inline-flex shrink-0 w-11 h-11">
            <div className="w-full h-full rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#9BD5F7] dark:bg-[#7DB9DE] flex items-center justify-center text-[#111111] dark:text-[#181815]">
              <Sparkles className="w-5 h-5" strokeWidth={2} />
            </div>
            {session.online && (
              <span
                aria-label="Online"
                className="absolute bottom-0 right-0 w-3 h-3 rounded-full bg-[#32B749] border-[1.5px] border-white dark:border-[#20201C]"
              />
            )}
          </div>

          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-[16px] font-semibold text-[#111111] dark:text-[#ECEBE4] leading-tight truncate">
                {session.name}
              </h2>
              {effectiveRepo && (
                <div className="hidden sm:inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] text-[10px] font-mono text-[#111111] dark:text-[#ECEBE4]">
                  <Github className="w-3 h-3" />
                  <span className="truncate max-w-[140px]">{effectiveRepo.name}</span>
                  <a
                    href={effectiveRepo.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-neutral-400 hover:text-black dark:hover:text-white"
                  >
                    <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                </div>
              )}
            </div>
            <p className="font-mono text-[11px] text-neutral-500 dark:text-[#A3A196] mt-0.5 truncate tabular-nums">
              {isTyping ? 'RepoMind is investigating...' : effectiveRepo ? `Context: ${effectiveRepo.name}` : session.lastSeen}
            </p>
          </div>
        </div>

        {/* Header right controls */}
        <div className="flex items-center gap-2.5 shrink-0">
          {effectiveRepo ? (
            <button
              type="button"
              onClick={onOpenRepoModal}
              className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] hover:bg-[#F7EA78]/40 font-mono text-[11px] text-[#111111] dark:text-[#ECEBE4] transition-colors cursor-pointer"
            >
              <GitBranch className="w-3 h-3 text-amber-600" />
              <span>Switch Repo</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={onOpenRepoModal}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border-[1.5px] border-amber-500 bg-amber-50 dark:bg-amber-950/30 font-mono text-[11px] font-medium text-amber-900 dark:text-amber-200 cursor-pointer"
            >
              <GitBranch className="w-3 h-3 text-amber-600" />
              <span>Link Repo</span>
            </button>
          )}

          <div className="relative">
            <button
              type="button"
              onClick={() => setShowHeaderMenu((prev) => !prev)}
              aria-label="Chat options"
              aria-expanded={showHeaderMenu}
              className="w-10 h-10 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] hover:brightness-95 flex items-center justify-center text-[#111111] dark:text-[#181815] transition-all duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111] dark:focus-visible:outline-[#ECEBE4] cursor-pointer"
            >
              <MoreHorizontal className="w-[18px] h-[18px]" strokeWidth={2} />
            </button>

            {showHeaderMenu && (
              <div
                role="menu"
                className="absolute right-0 top-12 z-20 w-52 rounded-2xl border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] py-1.5 shadow-[0_4px_0_0_#111111] dark:shadow-[0_4px_0_0_#0E0E0C]"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    onNewChat();
                    setShowHeaderMenu(false);
                  }}
                  className="w-full px-4 py-2.5 text-left text-xs font-mono text-[#111111] dark:text-[#ECEBE4] hover:bg-[#FAFAF5] dark:hover:bg-[#32322C] flex items-center gap-2.5 cursor-pointer"
                >
                  <SquarePen className="w-3.5 h-3.5" />
                  <span>New chat</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    onClearConversation();
                    setShowHeaderMenu(false);
                  }}
                  className="w-full px-4 py-2.5 text-left text-xs font-mono text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2.5 cursor-pointer"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Clear messages</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Message Stream */}
      <div className="flex-1 overflow-y-auto chat-scroll px-5 sm:px-7 py-6 space-y-5 bg-white dark:bg-[#20201C] transition-colors duration-150">
        {session.messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center py-12 gap-3">
            <div className="w-11 h-11 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-[#111111] dark:text-[#181815]" />
            </div>
            <p className="text-sm font-semibold text-[#111111] dark:text-[#ECEBE4]">
              Start an investigation with RepoMind
            </p>
            <p className="font-mono text-xs text-neutral-500 dark:text-[#A3A196] max-w-xs">
              Ask about repository architecture, bug root causes, change impact, or tests.
            </p>
          </div>
        ) : (
          session.messages.map((message) => {
            const isUser = message.sender === 'user';
            const messageRepo = message.repoTarget ?? effectiveRepo;
            const messageSources = !isUser ? getSourcesForMessage(message, messageRepo) : [];

            return (
              <div
                key={message.id}
                className={`flex flex-col ${
                  isUser ? 'items-end' : 'items-start'
                }`}
              >
                {message.attachment && (
                  <div className="mb-2 w-full max-w-[280px] sm:max-w-[295px] rounded-[18px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] p-3.5 shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C]">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#20201C] flex items-center justify-center shrink-0">
                        <FileText
                          className="w-4 h-4 text-[#5197F5] dark:text-[#7DB9DE]"
                          strokeWidth={2}
                        />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[13px] font-medium text-[#111111] dark:text-[#ECEBE4] truncate">
                          {message.attachment.fileName}
                        </p>
                        <p className="font-mono text-[11px] text-neutral-500 dark:text-[#A3A196] tabular-nums">
                          {message.attachment.fileSize}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {message.isError ? (
                  <div className="max-w-[84%] sm:max-w-[74%] rounded-[18px] border-[1.75px] border-[#111111] dark:border-red-900/70 bg-red-50 dark:bg-red-950/40 px-4 py-3 shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C]">
                    <div className="flex items-start gap-2.5">
                      <AlertCircle className="w-4 h-4 text-red-700 dark:text-red-400 shrink-0 mt-0.5" />
                      <div className="space-y-2">
                        <p className="font-mono text-[12px] leading-relaxed text-red-900 dark:text-red-200 break-words">
                          {message.text}
                        </p>
                        <button
                          type="button"
                          onClick={onRetryLast}
                          className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] hover:bg-neutral-100 dark:hover:bg-[#32322C] text-xs font-medium text-[#111111] dark:text-[#ECEBE4] cursor-pointer"
                        >
                          <RefreshCw className="w-3 h-3" />
                          <span>Retry</span>
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  (message.text || message.progressStep || message.isStreaming) && (
                    <div
                      className={`max-w-[88%] sm:max-w-[82%] rounded-[18px] border-[1.75px] border-[#111111] dark:border-[#42413A] px-4 py-3 shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C] overflow-hidden break-words ${
                        isUser
                          ? 'bg-white dark:bg-[#2E2E28]'
                          : 'bg-white dark:bg-[#262621]'
                      }`}
                    >
                      {/* 1. Live AiStatus: ONLY active when assistant is currently streaming and working on an investigation step */}
                      {!isUser && message.isStreaming && Boolean(message.progressStep) && (
                        <div className="mb-3">
                          <AiStatus
                            state={getAiStatusState(message.progressStep)}
                            label={message.progressStep}
                            timer={true}
                            className="font-mono text-xs"
                          />
                        </div>
                      )}

                      {/* 2. WebSearch: ONLY rendered when external web/docs search or verified citations were actually invoked */}
                      {!isUser && messageSources.length > 0 && !message.isStreaming && (
                        <div className="mb-3">
                          <WebSearch
                            query={messageRepo ? `${messageRepo.name} Verified Sources` : 'Verified Codebase Citations'}
                            sources={messageSources}
                            label={`${messageSources.length} verified ${messageSources.length === 1 ? 'source' : 'sources'}`}
                            status="done"
                            className="w-full font-mono text-xs"
                          />
                        </div>
                      )}

                      {/* 3. User Message Text */}
                      {isUser && message.text && (
                        <p className="font-mono text-[13px] leading-relaxed text-[#111111] dark:text-[#ECEBE4] whitespace-pre-wrap break-words">
                          {message.text}
                        </p>
                      )}

                      {/* 4. Assistant Message with StreamingResponse Component */}
                      {!isUser && message.text && (
                        <StreamingResponse
                          text={message.text}
                          status={message.isStreaming ? 'streaming' : 'done'}
                          actions={!message.isStreaming && !message.isError}
                          speed={40}
                          onRetry={onRetryLast}
                          className="font-mono text-[13px] leading-relaxed text-[#111111] dark:text-[#ECEBE4]"
                        />
                      )}
                    </div>
                  )
                )}

                <span className="mt-1.5 px-1 font-mono text-[11px] text-neutral-400 dark:text-[#8E8C82] tabular-nums">
                  {message.senderLabel} · {message.timestamp}
                </span>
              </div>
            );
          })
        )}

        {isTyping &&
          (!session.messages.length ||
            session.messages[session.messages.length - 1]?.sender === 'user') && (
            <div className="flex flex-col items-start">
              <div className="rounded-[18px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#262621] px-4 py-3 shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C]">
                <AiStatus
                  state="thinking"
                  label="RepoMind is investigating..."
                  timer={true}
                  className="font-mono text-xs"
                />
              </div>
              <span className="mt-1.5 px-1 font-mono text-[11px] text-neutral-400 dark:text-[#8E8C82]">
                Multi-agent LangGraph workflow running
              </span>
            </div>
          )}

        <div ref={streamEndRef} />
      </div>

      {/* Bottom Message Composer Bar */}
      <footer className="px-4 sm:px-6 py-3.5 border-t-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#20201C] shrink-0 relative transition-colors duration-150">
        {pendingAttachment && (
          <div className="mb-2.5 inline-flex items-center gap-2 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] px-3 py-1">
            <FileText className="w-3.5 h-3.5 text-[#5197F5] dark:text-[#7DB9DE]" />
            <span className="font-mono text-xs text-[#111111] dark:text-[#ECEBE4] truncate max-w-[200px]">
              {pendingAttachment.fileName}
            </span>
            <button
              type="button"
              onClick={() => setPendingAttachment(null)}
              aria-label="Remove attachment"
              className="p-0.5 rounded-full hover:bg-neutral-200 dark:hover:bg-[#35352E] text-[#111111] dark:text-[#ECEBE4] cursor-pointer"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}

        {showAttachMenu && (
          <div
            role="dialog"
            aria-label="Attach file or link repo"
            className="absolute bottom-[74px] right-16 z-20 w-68 rounded-2xl border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] p-2.5 shadow-[0_4px_0_0_#111111] dark:shadow-[0_4px_0_0_#0E0E0C]"
          >
            {onOpenRepoModal && (
              <>
                <button
                  type="button"
                  onClick={() => {
                    setShowAttachMenu(false);
                    onOpenRepoModal();
                  }}
                  className="w-full p-2 rounded-xl hover:bg-[#9BD5F7]/30 dark:hover:bg-[#7DB9DE]/20 flex items-center gap-2.5 text-left transition-colors cursor-pointer"
                >
                  <div className="w-6 h-6 rounded-md border border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] flex items-center justify-center shrink-0">
                    <Github className="w-3.5 h-3.5 text-[#111111] dark:text-[#181815]" />
                  </div>
                  <div>
                    <span className="text-xs font-semibold text-[#111111] dark:text-[#ECEBE4] block">
                      {effectiveRepo ? 'Switch GitHub Repo' : 'Link GitHub Repo'}
                    </span>
                    <span className="font-mono text-[10px] text-neutral-500 dark:text-[#A3A196]">
                      {effectiveRepo ? effectiveRepo.name : 'Target a repository'}
                    </span>
                  </div>
                </button>
                <div className="my-1.5 border-t border-neutral-200 dark:border-[#3A3932]" />
              </>
            )}

            <button
              type="button"
              onClick={() => {
                setShowAttachMenu(false);
                fileInputRef.current?.click();
              }}
              className="w-full px-3 py-2 rounded-xl hover:bg-[#F7EA78]/50 dark:hover:bg-[#D8CB68]/25 text-left text-xs font-medium text-[#111111] dark:text-[#ECEBE4] cursor-pointer flex items-center gap-2"
            >
              <FileText className="w-3.5 h-3.5 text-neutral-500" />
              <span>Upload text file (.txt, .md, .csv)</span>
            </button>
            <div className="my-1 border-t border-neutral-200 dark:border-[#3A3932]" />
            <p className="px-3 py-1 font-mono text-[11px] text-neutral-500 dark:text-[#A3A196]">
              Or attach a sample note
            </p>
            <div className="space-y-1">
              {SAMPLE_ATTACHMENTS.map((file) => (
                <button
                  key={file.fileName}
                  type="button"
                  onClick={() => {
                    setPendingAttachment(file);
                    setShowAttachMenu(false);
                  }}
                  className="w-full px-3 py-2 rounded-xl hover:bg-[#9BD5F7]/40 dark:hover:bg-[#7DB9DE]/20 flex items-center justify-between gap-2 text-left transition-colors cursor-pointer"
                >
                  <span className="text-xs font-medium text-[#111111] dark:text-[#ECEBE4] truncate">
                    {file.fileName}
                  </span>
                  <span className="font-mono text-[11px] text-neutral-500 dark:text-[#A3A196] tabular-nums shrink-0">
                    {file.fileSize}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.md,.csv,.json"
          onChange={handleFilePicked}
          className="hidden"
        />

        <form onSubmit={handleSubmit} className="flex items-center gap-3">
          <div className="flex-1 flex items-center h-11 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] pl-5 pr-3 focus-within:ring-2 focus-within:ring-[#111111]/20 dark:focus-within:ring-[#ECEBE4]/20 transition-all">
            <input
              ref={inputRef}
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="type your messages..."
              aria-label="Type your message"
              className="flex-1 bg-transparent font-mono text-[13px] text-[#111111] dark:text-[#ECEBE4] placeholder:text-neutral-400 dark:placeholder:text-[#7D7B70] focus:outline-none min-w-0"
            />
            <button
              type="button"
              onClick={() => setShowAttachMenu((prev) => !prev)}
              aria-label="Attach file"
              aria-expanded={showAttachMenu}
              className="w-8 h-8 rounded-full flex items-center justify-center text-[#111111] dark:text-[#ECEBE4] hover:bg-neutral-100 dark:hover:bg-[#35352E] transition-colors shrink-0 cursor-pointer"
            >
              <Paperclip className="w-[18px] h-[18px]" strokeWidth={1.85} />
            </button>
          </div>

          {draft.trim() || pendingAttachment ? (
            <button
              type="submit"
              disabled={isTyping}
              aria-label="Send message"
              className="w-11 h-11 rounded-full bg-[#111111] dark:bg-[#ECEBE4] hover:bg-[#2a2a2a] dark:hover:bg-[#D6D4CA] disabled:opacity-40 text-white dark:text-[#181815] flex items-center justify-center shrink-0 transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111] dark:focus-visible:outline-[#ECEBE4] cursor-pointer"
            >
              <SendHorizontal className="w-[18px] h-[18px]" strokeWidth={2} />
            </button>
          ) : (
            <button
              type="button"
              disabled={isTyping}
              onClick={handleVoiceShortcut}
              aria-label="Send quick prompt"
              title="Send quick prompt"
              className="w-11 h-11 rounded-full bg-[#111111] dark:bg-[#ECEBE4] hover:bg-[#2a2a2a] dark:hover:bg-[#D6D4CA] disabled:opacity-40 text-white dark:text-[#181815] flex items-center justify-center shrink-0 transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111] dark:focus-visible:outline-[#ECEBE4] cursor-pointer"
            >
              <Mic className="w-[18px] h-[18px]" strokeWidth={1.9} />
            </button>
          )}
        </form>
      </footer>
    </section>
  );
}
