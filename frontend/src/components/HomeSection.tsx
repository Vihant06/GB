import {useRef, useState} from 'react';
import {
  ArrowRight,
  ExternalLink,
  FileText,
  FolderGit2,
  GitBranch,
  Github,
  Lightbulb,
  ListChecks,
  Loader2,
  Paperclip,
  SendHorizontal,
  Sparkles,
  X,
} from 'lucide-react';
import {HOME_PROMPT_SUGGESTIONS, SAMPLE_ATTACHMENTS} from '../data/chatData';
import {PromptSuggestion, RepoTarget} from '../types/chat';

interface HomeSectionProps {
  existingMessageCount: number;
  activeRepo: RepoTarget | null;
  isIndexingRepo?: boolean;
  onIndexRepo: (repoUrl: string) => Promise<RepoTarget>;
  onClearActiveRepo: () => void;
  onStartNewChat: (
    prompt: string,
    attachment?: {fileName: string; fileSize: string; content?: string}
  ) => void;
  onResumeChat: () => void;
}

const POPULAR_REPOS = [
  {name: 'pallets/flask', desc: 'Lightweight WSGI web framework'},
  {name: 'fastapi/fastapi', desc: 'Modern high-performance web API framework'},
  {name: 'encode/starlette', desc: 'Async ASGI web framework toolkit'},
  {name: 'tiangolo/sqlmodel', desc: 'SQL databases in Python with Pydantic & SQLAlchemy'},
];

export function HomeSection({
  existingMessageCount,
  activeRepo,
  isIndexingRepo = false,
  onIndexRepo,
  onClearActiveRepo,
  onStartNewChat,
  onResumeChat,
}: HomeSectionProps) {
  const [prompt, setPrompt] = useState('');
  const [pendingAttachment, setPendingAttachment] = useState<{
    fileName: string;
    fileSize: string;
    content?: string;
  } | null>(null);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showRepoModal, setShowRepoModal] = useState(false);
  const [repoInput, setRepoInput] = useState('');
  const [repoError, setRepoError] = useState<string | null>(null);
  const [isLocalIndexing, setIsLocalIndexing] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed && !pendingAttachment) {
      return;
    }
    const finalPrompt =
      trimmed || `Please review the attached file: ${pendingAttachment?.fileName}`;
    onStartNewChat(finalPrompt, pendingAttachment ?? undefined);
    setPrompt('');
    setPendingAttachment(null);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
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

  const handleIndexSubmit = async (e?: React.FormEvent, customUrl?: string) => {
    e?.preventDefault();
    const targetUrl = (customUrl || repoInput).trim();
    if (!targetUrl) {
      setRepoError('Please enter a GitHub repository URL or slug.');
      return;
    }
    setRepoError(null);
    setIsLocalIndexing(true);
    try {
      await onIndexRepo(targetUrl);
      setShowRepoModal(false);
      setRepoInput('');
    } catch (err: any) {
      setRepoError(err.message || 'Failed to clone and index repository.');
    } finally {
      setIsLocalIndexing(false);
    }
  };

  const renderSuggestionIcon = (icon: PromptSuggestion['icon']) => {
    switch (icon) {
      case 'notes':
        return <FileText className="w-4 h-4 text-[#111111] dark:text-[#181815]" strokeWidth={1.9} />;
      case 'idea':
        return <Lightbulb className="w-4 h-4 text-[#111111] dark:text-[#181815]" strokeWidth={1.9} />;
      case 'spark':
        return <Sparkles className="w-4 h-4 text-[#111111] dark:text-[#181815]" strokeWidth={1.9} />;
      case 'checklist':
        return <ListChecks className="w-4 h-4 text-[#111111] dark:text-[#181815]" strokeWidth={1.9} />;
    }
  };

  const indexingActive = isIndexingRepo || isLocalIndexing;

  return (
    <section
      aria-label="New Chat Home"
      className="flex-1 flex flex-col items-center justify-center px-5 sm:px-10 py-8 overflow-y-auto chat-scroll bg-white dark:bg-[#20201C] transition-colors duration-150"
    >
      <div className="w-full max-w-[720px] flex flex-col items-center">
        {/* Editorial RepoMind Greeting */}
        <div className="text-center mb-6">
          <div className="inline-flex items-center justify-center w-11 h-11 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#9BD5F7] dark:bg-[#7DB9DE] shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C] mb-4">
            <Sparkles className="w-5 h-5 text-[#111111] dark:text-[#181815]" strokeWidth={2} />
          </div>
          <h2 className="text-2xl sm:text-[32px] font-semibold tracking-tight text-[#111111] dark:text-[#ECEBE4] leading-tight">
            RepoMind Codebase Intelligence
          </h2>
          <p className="mt-2 font-mono text-xs sm:text-[13px] text-neutral-500 dark:text-[#A3A196]">
            Multi-agent codebase analysis, AST symbol exploration, and verified RAG reports.
          </p>
        </div>

        {/* Dynamic Repository Status Card */}
        <div className="w-full mb-4">
          {activeRepo ? (
            <div className="w-full rounded-[18px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78]/30 dark:bg-[#D8CB68]/15 px-4 py-3 flex flex-wrap items-center justify-between gap-3 shadow-[0_2px_0_0_#111111] dark:shadow-[0_2px_0_0_#0E0E0C]">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#9BD5F7] dark:bg-[#7DB9DE] flex items-center justify-center shrink-0">
                  <Github className="w-4 h-4 text-[#111111] dark:text-[#181815]" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-[#111111] dark:text-[#ECEBE4] truncate">
                      {activeRepo.name}
                    </span>
                    <a
                      href={activeRepo.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-neutral-500 hover:text-black dark:hover:text-white"
                      title="Open repository on GitHub"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  </div>
                  <p className="font-mono text-[11px] text-neutral-600 dark:text-[#A3A196] truncate">
                    {(activeRepo.pythonFilesCount && activeRepo.pythonFilesCount > 0)
                      ? `${activeRepo.pythonFilesCount} Python files indexed · AST & RAG ready`
                      : `${activeRepo.filesCount || 0} code files indexed · AST & RAG ready`}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setShowRepoModal(true)}
                  className="px-3 py-1 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] hover:bg-neutral-100 dark:hover:bg-[#32322C] font-mono text-[11px] text-[#111111] dark:text-[#ECEBE4] transition-colors cursor-pointer"
                >
                  Change Repo
                </button>
                <button
                  type="button"
                  onClick={onClearActiveRepo}
                  className="p-1 rounded-full text-neutral-400 hover:text-red-600 dark:hover:text-red-400 cursor-pointer"
                  title="Unlink repository"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ) : (
            <div className="w-full rounded-[18px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#FFFBEB] dark:bg-amber-950/20 px-4 py-3 flex items-center justify-between gap-3 shadow-[0_2px_0_0_#111111] dark:shadow-[0_2px_0_0_#0E0E0C]">
              <div className="flex items-center gap-2.5 min-w-0">
                <GitBranch className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
                <span className="font-mono text-xs text-amber-900 dark:text-amber-200 truncate">
                  No repository linked yet. Link a GitHub repository first to start codebase Q&amp;A.
                </span>
              </div>
              <button
                type="button"
                onClick={() => setShowRepoModal(true)}
                className="px-3.5 py-1.5 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] hover:brightness-95 font-mono text-xs font-medium text-[#111111] dark:text-[#181815] shadow-[0_1.5px_0_0_#111111] dark:shadow-[0_1.5px_0_0_#0E0E0C] whitespace-nowrap shrink-0 cursor-pointer"
              >
                + Link Repo
              </button>
            </div>
          )}
        </div>

        {/* Gemini-style Prompt Composer Box */}
        <form
          onSubmit={handleSubmit}
          className="w-full rounded-[24px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] p-4 shadow-[0_4px_0_0_#111111] dark:shadow-[0_4px_0_0_#0E0E0C] relative transition-colors duration-150"
        >
          {pendingAttachment && (
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#20201C] px-3 py-1">
              <FileText className="w-3.5 h-3.5 text-[#5197F5] dark:text-[#7DB9DE]" />
              <span className="font-mono text-xs text-[#111111] dark:text-[#ECEBE4] truncate max-w-[200px]">
                {pendingAttachment.fileName}
              </span>
              <span className="font-mono text-[11px] text-neutral-500 dark:text-[#A3A196] tabular-nums">
                {pendingAttachment.fileSize}
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

          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            placeholder={
              activeRepo
                ? `Ask questions about ${activeRepo.name} (functions, bugs, tests, impact)...`
                : 'Link a repository or ask general Python questions...'
            }
            aria-label="Ask RepoMind anything"
            className="w-full resize-none bg-transparent font-mono text-[13px] sm:text-sm text-[#111111] dark:text-[#ECEBE4] placeholder:text-neutral-400 dark:placeholder:text-[#7D7B70] focus:outline-none leading-relaxed"
          />

          <div className="mt-3 pt-3 border-t border-neutral-200 dark:border-[#3A3932] flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 relative">
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.md,.csv,.json,.py"
                onChange={handleFilePicked}
                className="hidden"
              />

              {/* Attach Button with Popover Menu */}
              <button
                type="button"
                onClick={() => setShowAttachMenu((prev) => !prev)}
                aria-label="Attach file or link repo"
                aria-expanded={showAttachMenu}
                className="inline-flex items-center gap-1.5 h-9 px-3.5 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#20201C] hover:bg-[#F7EA78]/50 dark:hover:bg-[#D8CB68]/25 text-xs font-mono text-[#111111] dark:text-[#ECEBE4] transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <Paperclip className="w-3.5 h-3.5" strokeWidth={1.9} />
                <span>Attach</span>
              </button>

              {showAttachMenu && (
                <div
                  role="dialog"
                  aria-label="Attach options"
                  className="absolute left-0 bottom-11 sm:top-11 sm:bottom-auto z-30 w-72 rounded-2xl border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] p-2.5 shadow-[0_4px_0_0_#111111] dark:shadow-[0_4px_0_0_#0E0E0C]"
                >
                  {/* Option 1: Link GitHub Repository */}
                  <button
                    type="button"
                    onClick={() => {
                      setShowAttachMenu(false);
                      setShowRepoModal(true);
                    }}
                    className="w-full p-2.5 rounded-xl hover:bg-[#9BD5F7]/30 dark:hover:bg-[#7DB9DE]/20 flex items-start gap-2.5 text-left transition-colors cursor-pointer group"
                  >
                    <div className="w-7 h-7 rounded-lg border border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] flex items-center justify-center shrink-0 mt-0.5">
                      <Github className="w-4 h-4 text-[#111111] dark:text-[#181815]" />
                    </div>
                    <div>
                      <span className="text-xs font-semibold text-[#111111] dark:text-[#ECEBE4] block">
                        Link GitHub Repository
                      </span>
                      <span className="font-mono text-[10px] text-neutral-500 dark:text-[#A3A196]">
                        Clone and index AST symbols for RAG
                      </span>
                    </div>
                  </button>

                  <div className="my-1.5 border-t border-neutral-200 dark:border-[#3A3932]" />

                  {/* Option 2: Upload File */}
                  <button
                    type="button"
                    onClick={() => {
                      setShowAttachMenu(false);
                      fileInputRef.current?.click();
                    }}
                    className="w-full px-3 py-2 rounded-xl hover:bg-[#F7EA78]/50 dark:hover:bg-[#D8CB68]/25 text-left text-xs font-medium text-[#111111] dark:text-[#ECEBE4] cursor-pointer flex items-center gap-2"
                  >
                    <FileText className="w-3.5 h-3.5 text-neutral-500" />
                    <span>Upload file (.py, .txt, .md, .json)</span>
                  </button>

                  <div className="my-1.5 border-t border-neutral-200 dark:border-[#3A3932]" />
                  <p className="px-3 py-0.5 font-mono text-[10px] uppercase tracking-wider text-neutral-400 dark:text-[#7D7B70]">
                    Sample Notes
                  </p>
                  {SAMPLE_ATTACHMENTS.map((file) => (
                    <button
                      key={file.fileName}
                      type="button"
                      onClick={() => {
                        setPendingAttachment(file);
                        setShowAttachMenu(false);
                      }}
                      className="w-full px-3 py-1.5 rounded-xl hover:bg-neutral-100 dark:hover:bg-[#32322C] flex items-center justify-between gap-2 text-left transition-colors cursor-pointer"
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
              )}
            </div>

            <button
              type="submit"
              disabled={(!prompt.trim() && !pendingAttachment) || indexingActive}
              aria-label="Start new RepoMind investigation"
              className="w-10 h-10 rounded-full bg-[#111111] dark:bg-[#ECEBE4] hover:bg-[#2a2a2a] dark:hover:bg-[#D6D4CA] disabled:opacity-35 disabled:cursor-not-allowed text-white dark:text-[#181815] flex items-center justify-center shrink-0 transition-all duration-150 cursor-pointer"
            >
              <SendHorizontal className="w-[18px] h-[18px]" strokeWidth={2} />
            </button>
          </div>
        </form>

        {/* 4 Prompt Starter Cards */}
        <div className="w-full grid grid-cols-1 sm:grid-cols-2 gap-3 mt-5">
          {HOME_PROMPT_SUGGESTIONS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onStartNewChat(item.prompt)}
              className="group rounded-[18px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] hover:bg-[#9BD5F7]/45 dark:hover:bg-[#7DB9DE]/20 p-4 text-left shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C] transition-colors duration-150 flex items-start gap-3 cursor-pointer"
            >
              <span className="w-8 h-8 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] flex items-center justify-center shrink-0 mt-0.5">
                {renderSuggestionIcon(item.icon)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-semibold text-[#111111] dark:text-[#ECEBE4] leading-snug">
                  {item.title}
                </p>
                <p className="mt-1 font-mono text-[11px] text-neutral-600 dark:text-[#A3A196] line-clamp-2">
                  {item.prompt}
                </p>
              </div>
            </button>
          ))}
        </div>

        {/* Resume existing session shortcut */}
        {existingMessageCount > 0 && (
          <button
            type="button"
            onClick={onResumeChat}
            className="mt-6 inline-flex items-center gap-2 font-mono text-xs text-[#111111] dark:text-[#ECEBE4] hover:underline underline-offset-4 cursor-pointer"
          >
            <span>
              Resume active chat ({existingMessageCount}{' '}
              {existingMessageCount === 1 ? 'message' : 'messages'})
            </span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* GitHub Repository Link Dialog Modal */}
      {showRepoModal && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-xs"
        >
          <div className="w-full max-w-md rounded-[24px] border-[2px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#20201C] p-6 shadow-[0_8px_0_0_#111111] dark:shadow-[0_8px_0_0_#0E0E0C]">
            <div className="flex items-start justify-between gap-3 mb-4">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#9BD5F7] dark:bg-[#7DB9DE] flex items-center justify-center text-[#111111]">
                  <FolderGit2 className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-semibold text-base text-[#111111] dark:text-[#ECEBE4]">
                    Link GitHub Repository
                  </h3>
                  <p className="font-mono text-[11px] text-neutral-500 dark:text-[#A3A196]">
                    Clone and analyze AST code symbols for RAG
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  if (!indexingActive) {
                    setShowRepoModal(false);
                    setRepoError(null);
                  }
                }}
                disabled={indexingActive}
                className="p-1 rounded-full text-neutral-400 hover:text-black dark:hover:text-white cursor-pointer disabled:opacity-50"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleIndexSubmit} className="space-y-4">
              <div>
                <label className="block font-mono text-xs font-medium text-[#111111] dark:text-[#ECEBE4] mb-1.5">
                  GitHub Repository URL or Slug
                </label>
                <input
                  type="text"
                  value={repoInput}
                  onChange={(e) => setRepoInput(e.target.value)}
                  placeholder="e.g. owner/repo or https://github.com/owner/repo"
                  disabled={indexingActive}
                  className="w-full h-11 px-3.5 rounded-xl border-[1.5px] border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] font-mono text-xs text-[#111111] dark:text-[#ECEBE4] placeholder:text-neutral-400 focus:outline-none focus:ring-2 focus:ring-[#9BD5F7]"
                  autoFocus
                />
              </div>

              {/* Quick Picks */}
              <div>
                <span className="block font-mono text-[10px] uppercase text-neutral-400 dark:text-[#7D7B70] mb-1.5">
                  Or pick a popular repo:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {POPULAR_REPOS.map((r) => (
                    <button
                      key={r.name}
                      type="button"
                      disabled={indexingActive}
                      onClick={() => handleIndexSubmit(undefined, r.name)}
                      className="px-2.5 py-1 rounded-lg border border-neutral-300 dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] hover:bg-[#F7EA78]/60 dark:hover:bg-[#D8CB68]/20 font-mono text-[11px] text-[#111111] dark:text-[#ECEBE4] transition-colors cursor-pointer disabled:opacity-50"
                    >
                      {r.name}
                    </button>
                  ))}
                </div>
              </div>

              {repoError && (
                <div className="p-2.5 rounded-xl border border-red-300 dark:border-red-900 bg-red-50 dark:bg-red-950/30 font-mono text-xs text-red-700 dark:text-red-400">
                  {repoError}
                </div>
              )}

              {indexingActive && (
                <div className="p-3 rounded-xl border border-amber-300 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 flex items-center gap-2.5 font-mono text-xs text-amber-800 dark:text-amber-300">
                  <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                  <span>Cloning repository and indexing AST symbols... This may take a few moments.</span>
                </div>
              )}

              <div className="flex items-center justify-end gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => setShowRepoModal(false)}
                  disabled={indexingActive}
                  className="px-4 py-2 rounded-full border border-neutral-300 dark:border-[#42413A] font-mono text-xs text-[#111111] dark:text-[#ECEBE4] hover:bg-neutral-100 dark:hover:bg-[#282823] cursor-pointer disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={indexingActive || !repoInput.trim()}
                  className="px-5 py-2 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] hover:brightness-95 disabled:opacity-40 font-mono text-xs font-semibold text-[#111111] dark:text-[#181815] shadow-[0_2px_0_0_#111111] dark:shadow-[0_2px_0_0_#0E0E0C] cursor-pointer"
                >
                  {indexingActive ? 'Indexing...' : 'Index & Link'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
