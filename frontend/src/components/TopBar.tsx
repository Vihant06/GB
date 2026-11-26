import {ExternalLink, Github, GitBranch, SquarePen} from 'lucide-react';
import {RailSection, RepoTarget} from '../types/chat';
import {Avatar} from './Avatar';

interface TopBarProps {
  userName: string;
  activeSection: RailSection;
  hasMessages: boolean;
  activeRepo?: RepoTarget | null;
  onNewChat: () => void;
  onOpenRepoModal?: () => void;
}

export function TopBar({
  userName,
  activeSection,
  hasMessages,
  activeRepo,
  onNewChat,
  onOpenRepoModal,
}: TopBarProps) {
  return (
    <header className="flex items-center justify-between gap-3 sm:gap-6 shrink-0">
      {/* Left: User greeting */}
      <div className="flex items-center gap-3 min-w-0">
        <Avatar
          name={userName}
          bgColor="#D1D5DB"
          online
          size="md"
        />
        <h1 className="text-base sm:text-[20px] font-semibold tracking-tight text-[#111111] dark:text-[#ECEBE4] whitespace-nowrap truncate">
          Hi, {userName} 👋
        </h1>
      </div>

      {/* Center/Right: Active Repository Badge */}
      <div className="flex items-center gap-2.5 min-w-0">
        {activeRepo ? (
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] shadow-[0_1.5px_0_0_#111111] dark:shadow-[0_1.5px_0_0_#0E0E0C]">
            <Github className="w-3.5 h-3.5 text-[#111111] dark:text-[#ECEBE4] shrink-0" />
            <span className="font-mono text-xs font-semibold text-[#111111] dark:text-[#ECEBE4] max-w-[140px] sm:max-w-[220px] truncate">
              {activeRepo.name}
            </span>
            <span className="font-mono text-[10px] text-neutral-500 dark:text-[#A3A196] hidden sm:inline">
              ({activeRepo.filesCount || activeRepo.pythonFilesCount || 0} files)
            </span>
            <a
              href={activeRepo.url}
              target="_blank"
              rel="noreferrer"
              className="text-neutral-400 hover:text-black dark:hover:text-white"
              title="Open in GitHub"
            >
              <ExternalLink className="w-3 h-3" />
            </a>
            {onOpenRepoModal && (
              <button
                type="button"
                onClick={onOpenRepoModal}
                className="font-mono text-[10px] text-neutral-500 hover:text-black dark:hover:text-white underline ml-1 cursor-pointer"
              >
                change
              </button>
            )}
          </div>
        ) : onOpenRepoModal ? (
          <button
            type="button"
            onClick={onOpenRepoModal}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border-[1.5px] border-amber-500/80 bg-amber-50 dark:bg-amber-950/30 text-amber-900 dark:text-amber-200 font-mono text-xs hover:bg-amber-100 transition-colors cursor-pointer"
          >
            <GitBranch className="w-3.5 h-3.5 text-amber-600" />
            <span>Link Repo</span>
          </button>
        ) : null}

        {/* Right: New Chat trigger */}
        {activeSection === 'Chat' && hasMessages && (
          <button
            type="button"
            onClick={onNewChat}
            className="inline-flex items-center gap-2 h-9 px-3.5 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#F7EA78] dark:bg-[#D8CB68] hover:brightness-95 text-xs font-medium text-[#111111] dark:text-[#181815] shadow-[0_2px_0_0_#111111] dark:shadow-[0_2px_0_0_#0E0E0C] transition-all duration-150 whitespace-nowrap shrink-0 cursor-pointer"
          >
            <SquarePen className="w-3.5 h-3.5" strokeWidth={2} />
            <span className="hidden sm:inline">New Chat</span>
          </button>
        )}
      </div>
    </header>
  );
}
