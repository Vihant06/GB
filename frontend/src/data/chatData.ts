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
    title: 'Explain Session architecture',
    prompt:
      'Explain Session class architecture and how HTTPAdapter handles connection pooling in requests.',
    icon: 'notes',
  },
  {
    id: 'impact',
    title: 'Analyze change blast radius',
    prompt:
      'What is the blast radius and downstream callers if we modify Session.send method signature?',
    icon: 'idea',
  },
  {
    id: 'tests',
    title: 'Audit test coverage',
    prompt:
      'Find test cases covering connection retries and identify untested methods in sessions.py.',
    icon: 'checklist',
  },
  {
    id: 'debugging',
    title: 'Diagnose timeout issue',
    prompt:
      'Search recent GitHub Issues and CI failures regarding SSLError and connection timeouts.',
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
