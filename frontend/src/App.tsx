import {useEffect, useRef, useState} from 'react';
import {FolderGit2, Loader2, X} from 'lucide-react';
import {ChatSessionPane} from './components/ChatSessionPane';
import {HomeSection} from './components/HomeSection';
import {SettingsSection} from './components/SettingsSection';
import {TopBar} from './components/TopBar';
import {VisualSidebarRail} from './components/VisualSidebarRail';
import {SavedChats} from './components/SavedChats';
import {AuthPage, type AuthMode} from './components/AuthPage';
import {deleteSavedChat, describeHistoryError, listSavedChats, loadSavedMessages, renameSavedChat, saveChatMessage, savedChatRepo, supabase, workspaceAuthHeaders, type HistoryTarget, type SavedChat} from './lib/chatHistory';
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
const BACKEND_ORIGIN = (import.meta.env.VITE_BACKEND_URL ?? '').replace(/\/+$/, '');

function currentAuthMode(): AuthMode | null {
  if (window.location.hash === '#sign-in') return 'sign-in';
  if (window.location.hash === '#sign-up') return 'sign-up';
  return null;
}

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
  const [demoRepos, setDemoRepos] = useState<string[]>([]);
  const [modalRepoError, setModalRepoError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const [account, setAccount] = useState<{id: string; email?: string; name: string} | null>(null);
  const [authReady, setAuthReady] = useState(!supabase);
  const [authMode, setAuthMode] = useState<AuthMode | null>(currentAuthMode);
  const [showSavedChats, setShowSavedChats] = useState(false);
  const [savedChats, setSavedChats] = useState<SavedChat[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyListError, setHistoryListError] = useState<string | null>(null);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const accountIdRef = useRef<string | null>(null);
  const conversationRef = useRef<HistoryTarget | null>(null);
  const historyQueue = useRef(Promise.resolve());
  const deletedChats = useRef(new Set<string>());
  const openRequest = useRef(0);

  useEffect(() => {
    const syncAuthMode = () => setAuthMode(currentAuthMode());
    window.addEventListener('hashchange', syncAuthMode);
    return () => window.removeEventListener('hashchange', syncAuthMode);
  }, []);

  useEffect(() => {
    if (!supabase) return;
    const {data: {subscription}} = supabase.auth.onAuthStateChange((_event, session) => {
      const next = session?.user && !session.user.is_anonymous ? session.user : null;
      if ((next?.id ?? null) !== accountIdRef.current) {
        accountIdRef.current = next?.id ?? null;
        abortControllerRef.current?.abort();
        conversationRef.current = null;
        deletedChats.current.clear();
        setActiveRepo(null);
        setDemoRepos([]);
        setIsTyping(false);
        setIsIndexingRepo(false);
        setShowGlobalRepoModal(false);
        setShowSavedChats(Boolean(next) && window.matchMedia('(min-width: 640px)').matches);
        openRequest.current += 1;
        setSession({...INITIAL_CHAT_SESSION});
        setSavedChats([]);
        setHistoryError(null);
        setHistoryListError(null);
        setActiveSection('Home');
      }
      if (next) {
        const name = [next.user_metadata?.username, next.user_metadata?.full_name, next.email?.split('@')[0]]
          .find(value => typeof value === 'string' && value.trim());
        setAccount({id: next.id, email: next.email, name: name?.trim().slice(0, 80) || 'there'});
      } else {
        setAccount(null);
      }
      setAuthReady(true);
    });
    return () => subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!showSavedChats || !account) return;
    let disposed = false;
    setHistoryLoading(true);
    setHistoryListError(null);
    void historyQueue.current.then(() => listSavedChats(account.id)).then(chats => {
      if (!disposed) setSavedChats(chats);
    }).catch(error => {
      if (!disposed) setHistoryListError(describeHistoryError(error, 'Could not load chat history. Check your connection and retry.'));
    }).finally(() => {
      if (!disposed) setHistoryLoading(false);
    });
    return () => { disposed = true; };
  }, [showSavedChats, account?.id, historyRefresh]);

  const enqueueHistory = (target: HistoryTarget, ...messages: ChatMessage[]) => {
    historyQueue.current = historyQueue.current.then(async () => {
      for (const message of messages) {
        if (accountIdRef.current !== target.userId || deletedChats.current.has(target.id)) return;
        await saveChatMessage(target, message);
      }
      if (accountIdRef.current === target.userId) {
        setHistoryError(null);
        setHistoryRefresh(value => value + 1);
      }
    }).catch(error => {
      if (accountIdRef.current !== target.userId) return;
      setHistoryError(describeHistoryError(error, 'A chat message could not be saved. It remains visible in this tab.'));
      setRailNotice('Chat could not be saved.');
    });
  };

  const openSavedChat = async (chat: SavedChat) => {
    const userId = accountIdRef.current;
    if (!userId || deletedChats.current.has(chat.id)) return;
    const request = ++openRequest.current;
    setHistoryError(null);
    try {
      await historyQueue.current;
      const messages = await loadSavedMessages(chat.id, userId);
      if (accountIdRef.current !== userId || openRequest.current !== request || deletedChats.current.has(chat.id)) return;
      abortControllerRef.current?.abort();
      setIsTyping(false);
      const target = messages.length ? messages[messages.length - 1].repoTarget : savedChatRepo(chat);
      conversationRef.current = {id: chat.id, userId, title: chat.title, repo: savedChatRepo(chat), persisted: true};
      setActiveRepo(target ?? null);
      setSession({...INITIAL_CHAT_SESSION, id: chat.id, messages, repoTarget: target});
      if (!window.matchMedia('(min-width: 640px)').matches) setShowSavedChats(false);
      setActiveSection('Chat');
    } catch (error) {
      if (accountIdRef.current === userId && openRequest.current === request) {
        setHistoryError(describeHistoryError(error, 'Could not open this chat. Try selecting it again.'));
      }
    }
  };

  const signOut = async () => {
    if (!supabase) return;
    try {
      await historyQueue.current;
      const {error} = await supabase.auth.signOut({scope: 'local'});
      if (error) throw error;
      setShowSavedChats(false);
    } catch {
      setRailNotice('Could not sign out. Try again.');
    }
  };

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Ignore storage access errors
    }
  }, [theme]);

  // Load workspace data only after the signed-in account has been restored.
  useEffect(() => {
    if (!account) return;
    let cancelled = false;
    const fetchActiveRepo = async () => {
      try {
        const res = await fetch(`${BACKEND_ORIGIN}/repo/active`, {headers: await workspaceAuthHeaders()});
        if (cancelled) return;
        if (res.ok) {
          const data = await res.json();
          if (cancelled) return;
          if (Array.isArray(data.demo_repositories)) {
            setDemoRepos(data.demo_repositories.filter((repo: unknown): repo is string => typeof repo === 'string'));
          }
          if (data && data.repo_name && data.repo_available) {
            const target: RepoTarget = {
              name: data.repo_name,
              url: data.repo_url || `https://github.com/${data.repo_name}`,
              revision: data.repo_revision ?? undefined,
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
    void fetchActiveRepo();
    return () => { cancelled = true; };
  }, [account?.id]);

  const handleIndexRepo = async (repoUrl: string): Promise<RepoTarget> => {
    const accountId = accountIdRef.current;
    setIsIndexingRepo(true);
    setModalRepoError(null);
    try {
      const res = await fetch(`${BACKEND_ORIGIN}/repo/index`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...await workspaceAuthHeaders(),
        },
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
      if (!accountId || accountIdRef.current !== accountId) throw new Error('Your sign-in changed. Try again.');
      const target: RepoTarget = {
        name: data.repo_name,
        url: data.repo_url,
        revision: data.repo_revision,
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
    promptText: string,
    savedTarget?: HistoryTarget,
    originalMessage?: ChatMessage,
  ) => {
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setIsTyping(true);

    const historyPayload = historyMessages
      .filter((m, index, messages) => {
        if (m.isError || m.isStreaming || !m.text) return false;
        // Older user turns may lack a repository tag. Their immediate reply supplies the scope.
        const repo = m.repoTarget ?? (m.sender === 'user' && messages[index + 1]?.sender === 'contact'
          ? messages[index + 1].repoTarget : undefined);
        return repo?.url === activeRepo?.url && repo?.revision === activeRepo?.revision;
      })
      .slice(-6)
      .map((m) => ({
        role: m.sender === 'user' ? ('user' as const) : ('model' as const),
        text: (m.text ?? '').slice(0, 650),
      }));

    const botMsgId = crypto.randomUUID();
    const replyTimestamp = formatCurrentTime();
    let botMessageAdded = false;

    try {
      const backendUrl = `${BACKEND_ORIGIN}/query/stream`;

      const response = await fetch(backendUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...await workspaceAuthHeaders(),
        },
        body: JSON.stringify({
          query: promptText,
          repo_url: activeRepo?.url,
          repo_revision: activeRepo?.revision,
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
            node?: string;
            stage?: string;
            trace_logs?: string[];
            verified_citations?: string[];
            repo_revision?: string;
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
            const labels: Record<string, string> = {
              planning: 'Understanding your question',
              specialist: activeRepo ? 'Looking through the repository' : 'Looking for relevant information',
              verification: 'Checking the sources',
              synthesis: 'Writing your answer',
            };
            const taskLabels: Record<string, string> = {
              debugging: 'Checking where things went wrong',
              change_impact: 'Checking what the change would affect',
              test_intelligence: 'Looking for relevant tests',
            };
            const stepMsg = (payload.stage === 'specialist' && taskLabels[payload.node ?? ''])
              || labels[payload.stage ?? ''] || 'Getting your answer ready';
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
                progressStep: 'Writing your answer',
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
                    ? {...m, progressStep: 'Writing your answer', text: (m.text ?? '') + chunkText}
                    : m
                ),
              }));
            }
          }

          // Handle complete event
          if (eventType === 'complete') {
            const finalReport = payload.report;
            const repoTarget = activeRepo && payload.repo_revision
              ? {...activeRepo, revision: payload.repo_revision} : activeRepo ?? undefined;
            if (savedTarget && finalReport && !controller.signal.aborted) {
              const reply: ChatMessage = {
                id: botMsgId, sender: 'contact', senderLabel: 'RepoMind',
                timestamp: replyTimestamp, text: finalReport,
                verifiedCitations: payload.verified_citations, repoTarget,
              };
              enqueueHistory(savedTarget, ...(originalMessage ? [originalMessage] : []), reply);
            }
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
                      verifiedCitations: payload.verified_citations,
                      repoTarget: activeRepo && payload.repo_revision
                        ? {...activeRepo, revision: payload.repo_revision}
                        : m.repoTarget,
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
      if (abortControllerRef.current === controller) setIsTyping(false);
    }
  };

  const sendToSession = (
    text: string,
    attachment?: {fileName: string; fileSize: string; content?: string},
    resetFirst = false
  ) => {
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      sender: 'user',
      senderLabel: 'You',
      timestamp: formatCurrentTime(),
      text,
      repoTarget: activeRepo ?? undefined,
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

    if (resetFirst) {
      openRequest.current += 1;
      conversationRef.current = null;
    }
    const userId = accountIdRef.current;
    if (userId && !conversationRef.current) {
      conversationRef.current = {id: crypto.randomUUID(), userId, title: text.trim().slice(0, 90) || 'New chat', repo: activeRepo ?? undefined};
    }
    const savedTarget = conversationRef.current ?? undefined;
    setSession((prev) => ({
      ...prev,
      id: savedTarget?.id ?? prev.id,
      messages: [...baseMessages, userMsg],
    }));
    if (savedTarget) enqueueHistory(savedTarget, userMsg);
    void streamGeminiReply(baseMessages, promptWithAttachment, savedTarget, userMsg);
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

    void streamGeminiReply(priorHistory, promptText, conversationRef.current ?? undefined);
  };

  const handleNewChat = () => {
    openRequest.current += 1;
    conversationRef.current = null;
    abortControllerRef.current?.abort();
    setIsTyping(false);
    setSession({...INITIAL_CHAT_SESSION, repoTarget: activeRepo ?? undefined});
    setActiveSection('Home');
    if (!window.matchMedia('(min-width: 640px)').matches) setShowSavedChats(false);
  };

  const closeHistory = () => {
    openRequest.current += 1;
    setShowSavedChats(false);
  };

  const changeSavedChat = (chat: SavedChat, action: {kind: 'rename'; title: string} | {kind: 'delete'}): Promise<boolean> => {
    const userId = accountIdRef.current;
    if (!userId) return Promise.resolve(false);
    setHistoryError(null);
    // Apply mutations in the same queue as message writes so a pending reply
    // cannot recreate a conversation after it has been deleted.
    const result = historyQueue.current.then(async () => {
      if (accountIdRef.current !== userId || deletedChats.current.has(chat.id)) return false;
      if (action.kind === 'rename') {
        await renameSavedChat(chat.id, userId, action.title);
        if (accountIdRef.current !== userId) return false;
        if (conversationRef.current?.id === chat.id) conversationRef.current.title = action.title;
        setSavedChats(chats => chats.map(item => item.id === chat.id ? {...item, title: action.title} : item));
      } else {
        await deleteSavedChat(chat.id, userId);
        if (accountIdRef.current !== userId) return false;
        deletedChats.current.add(chat.id);
        setSavedChats(chats => chats.filter(item => item.id !== chat.id));
        if (conversationRef.current?.id === chat.id) handleNewChat();
      }
      setHistoryRefresh(value => value + 1);
      return true;
    }).catch(error => {
      if (accountIdRef.current === userId) {
        setHistoryError(describeHistoryError(error, `Could not ${action.kind} this chat. Try again.`));
      }
      return false;
    });
    historyQueue.current = result.then(() => undefined);
    return result;
  };

  const handleClearConversation = () => {
    conversationRef.current = null;
    abortControllerRef.current?.abort();
    setIsTyping(false);
    setSession({...INITIAL_CHAT_SESSION, repoTarget: activeRepo ?? undefined});
  };

  const handleRailSelect = (section: RailSection) => {
    if (section === 'Logout') {
      if (accountIdRef.current) {
        void signOut();
        return;
      }
      conversationRef.current = null;
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

  if (!authReady) {
    return <main className="grid min-h-screen place-items-center bg-[#FAFAF5] text-sm dark:bg-[#181815] dark:text-[#ECEBE4]">
      <p role="status">Checking your sign-in…</p>
    </main>;
  }
  if (!account) {
    const mode = authMode ?? 'sign-in';
    return <AuthPage key={mode} mode={mode} />;
  }

  return (
    <div className="h-screen w-screen overflow-hidden bg-[#FAFAF5] dark:bg-[#181815] p-3 sm:p-5 lg:p-6 flex flex-col-reverse sm:flex-row items-stretch gap-3 sm:gap-4 lg:gap-5 transition-colors duration-150">
      {/* Navigation rail */}
      <VisualSidebarRail
        activeSection={activeSection}
        hasActiveChat={session.messages.length > 0}
        onSelectSection={handleRailSelect}
        historyOpen={showSavedChats}
        onToggleHistory={() => showSavedChats ? closeHistory() : setShowSavedChats(true)}
      />

      {showSavedChats && (
        <SavedChats
          chats={savedChats} activeChatId={session.id} loading={historyLoading} error={historyListError ?? historyError}
          onClose={closeHistory} onNewChat={handleNewChat}
          onOpen={chat => void openSavedChat(chat)}
          onRename={(chat, title) => changeSavedChat(chat, {kind: 'rename', title})}
          onDelete={chat => changeSavedChat(chat, {kind: 'delete'})}
          onRetry={() => { setHistoryError(null); setHistoryRefresh(value => value + 1); }}
        />
      )}

      {/* Main workspace column */}
      <main className="flex-1 flex flex-col gap-3.5 sm:gap-4 min-w-0 min-h-0 h-full relative">
        <TopBar
          userName={account.name}
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

              <p className="text-xs">Your account can index up to 5 distinct public repositories every 365 days.</p>

              {/* Quick Picks */}
              {demoRepos.length > 0 && <div>
                <span className="block font-mono text-[10px] uppercase text-neutral-400 dark:text-[#7D7B70] mb-1.5">
                  Or pick a demo repo:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {demoRepos.map((repo) => (
                    <button
                      key={repo}
                      type="button"
                      disabled={isIndexingRepo}
                      onClick={() => handleIndexRepo(repo).catch(() => {})}
                      className="px-2.5 py-1 rounded-lg border border-neutral-300 dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] hover:bg-[#F7EA78]/60 dark:hover:bg-[#D8CB68]/20 font-mono text-[11px] text-[#111111] dark:text-[#ECEBE4] transition-colors cursor-pointer disabled:opacity-50"
                    >
                      {repo}
                    </button>
                  ))}
                </div>
              </div>}

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
