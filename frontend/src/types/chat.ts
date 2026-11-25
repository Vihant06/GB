export type RailSection = 'Home' | 'Chat' | 'Settings' | 'Logout';

export type ResponseStyle = 'balanced' | 'concise' | 'detailed';

export type ThemeMode = 'light' | 'dark';

export interface FileAttachment {
  id: string;
  fileName: string;
  fileSize: string;
  content?: string;
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'contact';
  senderLabel: string;
  timestamp: string;
  text?: string;
  attachment?: FileAttachment;
  isStreaming?: boolean;
  isError?: boolean;
  progressStep?: string;
  traceLogs?: string[];
  verifiedCitations?: string[];
  repoTarget?: RepoTarget;
}

export interface RepoTarget {
  name: string;
  url: string;
  revision?: string;
  filesCount?: number;
  pythonFilesCount?: number;
  docsFilesCount?: number;
  isIndexing?: boolean;
  indexed?: boolean;
}

export interface ChatSession {
  id: string;
  name: string;
  shortName: string;
  online: boolean;
  lastSeen: string;
  messages: ChatMessage[];
  repoTarget?: RepoTarget;
}

export interface PromptSuggestion {
  id: string;
  title: string;
  prompt: string;
  icon: 'notes' | 'idea' | 'spark' | 'checklist';
}
