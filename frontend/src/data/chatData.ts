import {ChatSession, PromptSuggestion} from '../types/chat';

export const CURRENT_USER = {
  name: 'Wayne',
  avatar: '/src/assets/images/avatar_wayne_1795282537150.jpg',
  online: true,
};

export const SAMPLE_ATTACHMENTS: Array<{
  fileName: string;
  fileSize: string;
  content: string;
}> = [
  {
    fileName: 'Project_Notes.txt',
    fileSize: '1.12KB',
    content:
      'Q4 Launch Priorities:\n1. Finalize onboarding checklist\n2. Review latency telemetry on /api/chat\n3. Prepare Friday stakeholder demo slides',
  },
  {
    fileName: 'Meeting_Brief.md',
    fileSize: '840B',
    content:
      '# Weekly Sync\n- Goal: Ship streamlined 1-to-1 chat workspace\n- Open items: Copy polish, keyboard shortcuts, mobile responsiveness',
  },
];

export const HOME_PROMPT_SUGGESTIONS: PromptSuggestion[] = [
  {
    id: 'architecture',
    title: 'Explain system architecture',
    prompt:
      'Explain the high-level architecture, module boundaries, and entry point flows of this codebase.',
    icon: 'notes',
  },
  {
    id: 'impact',
    title: 'Analyze change blast radius',
    prompt:
      'What is the blast radius and downstream callers if we modify the core data model or public API interfaces?',
    icon: 'idea',
  },
  {
    id: 'tests',
    title: 'Audit test coverage',
    prompt:
      'Analyze test suite coverage across key modules and identify critical untested functions and methods.',
    icon: 'checklist',
  },
  {
    id: 'debugging',
    title: 'Diagnose recent issues',
    prompt:
      'Search recent GitHub Issues, pull requests, and CI workflow errors for unresolved failure patterns.',
    icon: 'spark',
  },
];

export const INITIAL_CHAT_SESSION: ChatSession = {
  id: 'repomind-chat',
  name: 'RepoMind',
  shortName: 'RepoMind',
  online: true,
  lastSeen: 'Online now',
  messages: [],
};
