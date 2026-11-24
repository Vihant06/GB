import {useEffect, useRef, useState} from 'react';
import {FolderGit2, Loader2, X} from 'lucide-react';
import {ChatSessionPane} from './components/ChatSessionPane';
import {HomeSection} from './components/HomeSection';
import {SettingsSection} from './components/SettingsSection';
import {TopBar} from './components/TopBar';
import {VisualSidebarRail} from './components/VisualSidebarRail';
import {INITIAL_CHAT_SESSION} from './data/chatData';
import {
  ChatMessage,
  ChatSession,
  RailSection,
  RepoTarget,
  ResponseStyle,
  ThemeMode,
} from './types/chat';

const THEME_STORAGE_KEY = 'wayne-chat-theme';

const POPULAR_REPOS = [
  {name: 'pallets/flask', desc: 'Lightweight WSGI Python web framework'},
  {name: 'fastapi/fastapi', desc: 'Modern high-performance web API framework'},
  {name: 'encode/starlette', desc: 'Async ASGI web framework toolkit'},
  {name: 'tiangolo/sqlmodel', desc: 'SQL databases in Python with Pydantic & SQLAlchemy'},
];

function getInitialTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    if (saved === 'dark' || saved === 'light') {
      return saved;
    }
  } catch {
    // Ignore storage access errors
  }
  return 'light';
}

function formatCurrentTime(): string {
  const now = new Date();
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  return `${hours}:${minutes}`;
}

export default function App() {
  const [activeSection, setActiveSection] = useState<RailSection>('Home');
  const [session, setSession] = useState<ChatSession>(INITIAL_CHAT_SESSION);
  const [responseStyle, setResponseStyle] = useState<ResponseStyle>('balanced');
  const [theme, setTheme] = useState<ThemeMode>(getInitialTheme);
  const [isTyping, setIsTyping] = useState<boolean>(false);
  const [railNotice, setRailNotice] = useState<string | null>(null);

  // Dynamic GitHub Repository target state
  const [activeRepo, setActiveRepo] = useState<RepoTarget | null>(null);
  const [isIndexingRepo, setIsIndexingRepo] = useState<boolean>(false);
  const [showGlobalRepoModal, setShowGlobalRepoModal] = useState<boolean>(false);
  const [modalRepoInput, setModalRepoInput] = useState<string>('');
  const [modalRepoError, setModalRepoError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Ignore storage access errors
    }
  }, [theme]);

  // Load currently active repository on mount
  useEffect(() => {
    const fetchActiveRepo = async () => {
      try {
        const res = await fetch('/repo/active');
        if (res.ok) {
          const data = await res.json();
          if (data && data.repo_name && data.repo_available) {
            const target: RepoTarget = {
              name: data.repo_name,
              url: data.repo_url || `https://github.com/${data.repo_name}`,
              filesCount: data.total_files_count || data.python_files_count || 0,
              pythonFilesCount: data.python_files_count || 0,
              docsFilesCount: data.docs_files_count || 0,
              indexed: true,
            };
            setActiveRepo(target);
            setSession((prev) => ({...prev, repoTarget: target}));
          }
        }
      } catch {
        // Backend might still be warming up
      }
    };
    fetchActiveRepo();
  }, []);

  const handleIndexRepo = async (repoUrl: string): Promise<RepoTarget> => {
    setIsIndexingRepo(true);
    setModalRepoError(null);
    try {
      const res = await fetch('/repo/index', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({repo_url: repoUrl}),
      });

      if (!res.ok) {
        let errMessage = 'Failed to clone and index repository.';
        try {
          const errJson = await res.json();
          if (errJson.detail) errMessage = errJson.detail;
          else if (errJson.error) errMessage = errJson.error;
        } catch {}
        throw new Error(errMessage);
      }

      const data = await res.json();
      const target: RepoTarget = {
        name: data.repo_name,
        url: data.repo_url,
        filesCount: data.total_files_count,
        pythonFilesCount: data.python_files_count,
        docsFilesCount: data.docs_files_count,
        indexed: true,
      };

      setActiveRepo(target);
      setSession((prev) => ({...prev, repoTarget: target}));
      setRailNotice(`Repository ${data.repo_name} linked (${data.total_files_count || data.python_files_count} files).`);
      window.setTimeout(() => setRailNotice(null), 3500);
      setShowGlobalRepoModal(false);
      setModalRepoInput('');
      return target;
    } catch (err: any) {
      setModalRepoError(err.message || 'Failed to clone and index repository.');
      throw err;
    } finally {
      setIsIndexingRepo(false);
    }
  };

  const handleClearActiveRepo = () => {
    setActiveRepo(null);
    setSession((prev) => ({...prev, repoTarget: undefined}));
    setRailNotice('Repository unlinked.');
    window.setTimeout(() => setRailNotice(null), 2400);
  };

  const streamGeminiReply = async (
    historyMessages: ChatMessage[],
    promptText: string
  ) => {
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setIsTyping(true);

    const historyPayload = historyMessages
      .filter((m) => !m.isError && m.text)
      .map((m) => ({
        role: m.sender === 'user' ? ('user' as const) : ('model' as const),
        text: m.attachment?.content
          ? `${m.text}\n\n[Attached File: ${m.attachment.fileName}]\n${m.attachment.content}`
          : (m.text ?? ''),
      }));

    const botMsgId = `gemini-reply-${Date.now()}`;
    const replyTimestamp = formatCurrentTime();
    let botMessageAdded = false;

    try {
      const backendUrl = import.meta.env.VITE_BACKEND_URL
        ? `${import.meta.env.VITE_BACKEND_URL}/query/stream`
        : '/query/stream';

      const response = await fetch(backendUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          query: promptText,
          repo_url: activeRepo?.url,
          history: historyPayload,
          response_style: responseStyle,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        let errMessage = 'Failed to reach RepoMind API.';
        try {
          const errJson = (await response.json()) as {detail?: string; error?: string};
          if (errJson.detail) {
            errMessage = errJson.detail;
          } else if (errJson.error) {
            errMessage = errJson.error;
          }
        } catch {
          errMessage = `Server returned status ${response.status}.`;
        }
        throw new Error(errMessage);
      }

      if (!response.body) {
        throw new Error('Streaming response body is unavailable.');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const {done, value} = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, {stream: true});
        const events = buffer.split('\n\n');
        buffer = events.pop() ?? '';

        for (const eventChunk of events) {
          if (!eventChunk.trim()) {
            continue;
          }
          const lines = eventChunk.split('\n');
          let eventType = 'message';
          let dataText = '';
          for (const l of lines) {
            if (l.startsWith('event: ')) {
              eventType = l.slice(7).trim();
            } else if (l.startsWith('data: ')) {
              dataText = l.slice(6).trim();
            }
          }
          if (!dataText) {
            continue;
          }

          let payload: {
            text?: string;
            token?: string;
            report?: string;
            message?: string;
            node?: string;
            trace_logs?: string[];
            error?: string;
            done?: boolean;
          } = {};
          try {
            payload = JSON.parse(dataText);
          } catch {
            continue;
          }

          if (payload.error) {
            throw new Error(payload.error);
          }

          // Handle progress/status events
          if (eventType === 'progress' || eventType === 'status') {
            const stepMsg = payload.message || '';
            if (!botMessageAdded) {
              botMessageAdded = true;
              const initialBotMsg: ChatMessage = {
                id: botMsgId,
                sender: 'contact',
                senderLabel: 'RepoMind',
                timestamp: replyTimestamp,
                text: '',
                isStreaming: true,
                progressStep: stepMsg,
                repoTarget: activeRepo ?? undefined,
              };
              setSession((prev) => ({
                ...prev,
                messages: [...prev.messages, initialBotMsg],
              }));
            } else {
              setSession((prev) => ({
                ...prev,
                messages: prev.messages.map((m) =>
                  m.id === botMsgId ? {...m, progressStep: stepMsg} : m
                ),
              }));
            }
          }

          // Handle token chunks
          const chunkText = payload.token ?? payload.text ?? '';
          if (chunkText) {
            if (!botMessageAdded) {
              botMessageAdded = true;
              const initialBotMsg: ChatMessage = {
                id: botMsgId,
                sender: 'contact',
                senderLabel: 'RepoMind',
                timestamp: replyTimestamp,
                text: chunkText,
                isStreaming: true,
                repoTarget: activeRepo ?? undefined,
              };
              setSession((prev) => ({
                ...prev,
                messages: [...prev.messages, initialBotMsg],
              }));
            } else {
              setSession((prev) => ({
                ...prev,
                messages: prev.messages.map((m) =>
                  m.id === botMsgId
                    ? {...m, text: (m.text ?? '') + chunkText}
                    : m
                ),
              }));
            }
          }

          // Handle complete event
          if (eventType === 'complete') {
            const finalReport = payload.report;
            setSession((prev) => ({
              ...prev,
              messages: prev.messages.map((m) =>
                m.id === botMsgId
                  ? {
                      ...m,
                      text: finalReport || m.text,
                      isStreaming: false,
                      progressStep: undefined,
                      traceLogs: payload.trace_logs,
                    }
                  : m
              ),
            }));
          }
        }
      }

      setSession((prev) => ({
        ...prev,
        messages: prev.messages.map((m) =>
          m.id === botMsgId ? {...m, isStreaming: false, progressStep: undefined} : m
        ),
      }));
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        return;
      }
      const messageText =
        err instanceof Error
          ? err.message
          : 'Unable to complete RepoMind API request.';

      setSession((prev) => {
        const filtered = prev.messages.filter((m) => m.id !== botMsgId);
        return {
          ...prev,
          messages: [
            ...filtered,
            {
              id: `bot-err-${Date.now()}`,
              sender: 'contact',
              senderLabel: 'RepoMind',
              timestamp: formatCurrentTime(),
              text: messageText,
              isError: true,
            },
          ],
        };
      });
    } finally {
      setIsTyping(false);
    }
  };

  const sendToSession = (
    text: string,
    attachment?: {fileName: string; fileSize: string; content?: string},
    resetFirst = false
  ) => {
    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      senderLabel: 'You',
      timestamp: formatCurrentTime(),
      text,
      attachment: attachment
        ? {
            id: `att-${Date.now()}`,
            fileName: attachment.fileName,
            fileSize: attachment.fileSize,
            content: attachment.content,
          }
        : undefined,
    };

    const baseMessages = resetFirst
      ? []
      : session.messages.filter((m) => !m.isError);
    const promptWithAttachment = attachment?.content
      ? `${text}\n\n[Attached File: ${attachment.fileName}]\n${attachment.content}`
      : attachment
      ? `${text} (Attached file: ${attachment.fileName})`
      : text;

    setSession((prev) => ({
      ...prev,
      messages: [...baseMessages, userMsg],
    }));

    void streamGeminiReply(baseMessages, promptWithAttachment);
  };

  const handleStartNewChatFromHome = (
    prompt: string,
    attachment?: {fileName: string; fileSize: string; content?: string}
  ) => {
    setActiveSection('Chat');
    sendToSession(prompt, attachment, true);
  };

  const handleSendMessageInChat = (
    text: string,
    attachment?: {fileName: string; fileSize: string; content?: string}
  ) => {
    sendToSession(text, attachment, false);
  };

  const handleRetryLast = () => {
    const cleanMessages = session.messages.filter((m) => !m.isError);
    const lastUserIndex = [...cleanMessages]
      .reverse()
      .findIndex((m) => m.sender === 'user');
    if (lastUserIndex === -1) {
      return;
    }
    const actualIdx = cleanMessages.length - 1 - lastUserIndex;
    const lastUserMsg = cleanMessages[actualIdx];
    const priorHistory = cleanMessages.slice(0, actualIdx);

    setSession((prev) => ({
      ...prev,
      messages: cleanMessages,
    }));

    const promptText = lastUserMsg.attachment?.content
      ? `${lastUserMsg.text}\n\n[Attached File: ${lastUserMsg.attachment.fileName}]\n${lastUserMsg.attachment.content}`
      : (lastUserMsg.text ?? '');

    void streamGeminiReply(priorHistory, promptText);
  };

  const handleNewChat = () => {
    abortControllerRef.current?.abort();
    setIsTyping(false);
    setSession({...INITIAL_CHAT_SESSION, repoTarget: activeRepo ?? undefined});
    setActiveSection('Home');
  };

  const handleClearConversation = () => {
    abortControllerRef.current?.abort();
    setIsTyping(false);
    setSession({...INITIAL_CHAT_SESSION, repoTarget: activeRepo ?? undefined});
  };

  const handleRailSelect = (section: RailSection) => {
    if (section === 'Logout') {
      abortControllerRef.current?.abort();
      setIsTyping(false);
      setSession({...INITIAL_CHAT_SESSION, repoTarget: activeRepo ?? undefined});
      setActiveSection('Home');
      setRailNotice('Session reset.');
      window.setTimeout(() => setRailNotice(null), 2400);
      return;
    }
    setRailNotice(null);
    setActiveSection(section);
  };

  return (
    <div className="h-screen w-screen overflow-hidden bg-[#FAFAF5] dark:bg-[#181815] p-3 sm:p-5 lg:p-6 flex flex-col-reverse sm:flex-row items-stretch gap-3 sm:gap-4 lg:gap-5 transition-colors duration-150">
      {/* Navigation rail */}
      <VisualSidebarRail
        activeSection={activeSection}
        hasActiveChat={session.messages.length > 0}
        onSelectSection={handleRailSelect}
      />

      {/* Main workspace column */}
      <main className="flex-1 flex flex-col gap-3.5 sm:gap-4 min-w-0 min-h-0 h-full relative">
        <TopBar
          activeSection={activeSection}
          hasMessages={session.messages.length > 0}
          activeRepo={activeRepo}
          onNewChat={handleNewChat}
          onOpenRepoModal={() => setShowGlobalRepoModal(true)}
        />

        {railNotice && (
          <div
            role="status"
            className="absolute bottom-24 right-4 z-30 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] px-4 py-1.5 font-mono text-xs text-[#111111] dark:text-[#181815] shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C]"
          >
            {railNotice}
          </div>
        )}

        {/* Main View Container */}
        <div className="flex-1 min-h-0 rounded-[24px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#20201C] overflow-hidden flex transition-colors duration-150">
          {activeSection === 'Home' ? (
            <HomeSection
              existingMessageCount={session.messages.length}
              activeRepo={activeRepo}
              isIndexingRepo={isIndexingRepo}
              onIndexRepo={handleIndexRepo}
              onClearActiveRepo={handleClearActiveRepo}
              onStartNewChat={handleStartNewChatFromHome}
              onResumeChat={() => setActiveSection('Chat')}
            />
          ) : activeSection === 'Settings' ? (
            <SettingsSection
              theme={theme}
              onChangeTheme={setTheme}
              responseStyle={responseStyle}
              onChangeResponseStyle={setResponseStyle}
              onClearChat={() => {
                handleClearConversation();
                setRailNotice('Chat messages cleared.');
                window.setTimeout(() => setRailNotice(null), 2200);
              }}
            />
          ) : (
            <ChatSessionPane
              session={session}
              activeRepo={activeRepo}
              isTyping={isTyping}
              onSendMessage={handleSendMessageInChat}
              onRetryLast={handleRetryLast}
              onNewChat={handleNewChat}
              onClearConversation={handleClearConversation}
              onOpenRepoModal={() => setShowGlobalRepoModal(true)}
            />
          )}
        </div>
      </main>

      {/* Global Repository Link Modal */}
      {showGlobalRepoModal && (
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
                    Target GitHub Repository
                  </h3>
                  <p className="font-mono text-[11px] text-neutral-500 dark:text-[#A3A196]">
                    Clone and analyze AST code symbols for RAG
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  if (!isIndexingRepo) {
                    setShowGlobalRepoModal(false);
                    setModalRepoError(null);
                  }
                }}
                disabled={isIndexingRepo}
                className="p-1 rounded-full text-neutral-400 hover:text-black dark:hover:text-white cursor-pointer disabled:opacity-50"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (modalRepoInput.trim()) {
                  handleIndexRepo(modalRepoInput.trim()).catch(() => {});
                }
              }}
              className="space-y-4"
            >
              <div>
                <label className="block font-mono text-xs font-medium text-[#111111] dark:text-[#ECEBE4] mb-1.5">
                  GitHub Repository URL or Slug
                </label>
                <input
                  type="text"
                  value={modalRepoInput}
                  onChange={(e) => setModalRepoInput(e.target.value)}
                  placeholder="e.g. owner/repo or https://github.com/owner/repo"
                  disabled={isIndexingRepo}
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
                      disabled={isIndexingRepo}
                      onClick={() => handleIndexRepo(r.name).catch(() => {})}
                      className="px-2.5 py-1 rounded-lg border border-neutral-300 dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] hover:bg-[#F7EA78]/60 dark:hover:bg-[#D8CB68]/20 font-mono text-[11px] text-[#111111] dark:text-[#ECEBE4] transition-colors cursor-pointer disabled:opacity-50"
                    >
                      {r.name}
                    </button>
                  ))}
                </div>
              </div>

              {modalRepoError && (
                <div className="p-2.5 rounded-xl border border-red-300 dark:border-red-900 bg-red-50 dark:bg-red-950/30 font-mono text-xs text-red-700 dark:text-red-400">
                  {modalRepoError}
                </div>
              )}

              {isIndexingRepo && (
                <div className="p-3 rounded-xl border border-amber-300 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 flex items-center gap-2.5 font-mono text-xs text-amber-800 dark:text-amber-300">
                  <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                  <span>Cloning repository and indexing AST symbols... This may take a few moments.</span>
                </div>
              )}

              <div className="flex items-center justify-end gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => setShowGlobalRepoModal(false)}
                  disabled={isIndexingRepo}
                  className="px-4 py-2 rounded-full border border-neutral-300 dark:border-[#42413A] font-mono text-xs text-[#111111] dark:text-[#ECEBE4] hover:bg-neutral-100 dark:hover:bg-[#282823] cursor-pointer disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isIndexingRepo || !modalRepoInput.trim()}
                  className="px-5 py-2 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] hover:brightness-95 disabled:opacity-40 font-mono text-xs font-semibold text-[#111111] dark:text-[#181815] shadow-[0_2px_0_0_#111111] dark:shadow-[0_2px_0_0_#0E0E0C] cursor-pointer"
                >
                  {isIndexingRepo ? 'Indexing...' : 'Index & Link'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
