import {History, Home, LogOut, MessageSquare, Settings} from 'lucide-react';
import {RailSection} from '../types/chat';

interface VisualSidebarRailProps {
  activeSection: RailSection;
  hasActiveChat: boolean;
  onSelectSection: (section: RailSection) => void;
  historyOpen: boolean;
  onToggleHistory: () => void;
}

export function VisualSidebarRail({
  activeSection,
  hasActiveChat,
  onSelectSection,
  historyOpen,
  onToggleHistory,
}: VisualSidebarRailProps) {
  const navItems: Array<{
    id: RailSection;
    label: string;
    renderIcon: (isActive: boolean) => React.ReactNode;
  }> = [
    {
      id: 'Home',
      label: 'Home',
      renderIcon: (isActive) => (
        <div className="relative inline-flex items-center justify-center">
          <Home
            className="w-5 h-5"
            strokeWidth={isActive ? 2.1 : 1.6}
            fill={isActive ? 'currentColor' : 'none'}
          />
          {isActive && (
            <span className="absolute -top-0.5 -right-1 w-2.5 h-2.5 rounded-full bg-[#32B749] border-[1.5px] border-white dark:border-[#20201C]" />
          )}
        </div>
      ),
    },
    {
      id: 'Chat',
      label: 'Chat',
      renderIcon: (isActive) => (
        <div className="relative inline-flex items-center justify-center">
          <MessageSquare
            className="w-5 h-5"
            strokeWidth={isActive ? 2.1 : 1.6}
            fill={isActive ? 'currentColor' : 'none'}
          />
          {isActive && (
            <>
              <span className="absolute w-2.5 h-[1.5px] bg-white dark:bg-[#20201C] rounded-full -translate-y-[2px]" />
              <span className="absolute w-1.5 h-[1.5px] bg-white dark:bg-[#20201C] rounded-full translate-y-[1.5px] -translate-x-[2px]" />
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-[#32B749] border-[1.5px] border-white dark:border-[#20201C]" />
            </>
          )}
          {!isActive && hasActiveChat && (
            <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-[#5197F5] dark:bg-[#7DB9DE] border border-white dark:border-[#20201C]" />
          )}
        </div>
      ),
    },
    {
      id: 'Settings',
      label: 'Settings',
      renderIcon: (isActive) => (
        <Settings
          className="w-5 h-5"
          strokeWidth={isActive ? 2.2 : 1.6}
        />
      ),
    },
  ];

  return (
    <aside
      aria-label="Workspace navigation"
      className="flex sm:flex-col items-center justify-between w-full sm:w-[72px] shrink-0 rounded-full sm:rounded-[40px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#20201C] py-2 px-4 sm:py-7 sm:px-2 select-none transition-colors duration-150"
    >
      <nav className="flex sm:flex-col items-center justify-around sm:justify-start gap-2 sm:gap-6 flex-1 sm:flex-none w-full">
        {navItems.map((item) => {
          const isActive = activeSection === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelectSection(item.id)}
              aria-current={isActive ? 'page' : undefined}
              className={`group flex flex-col items-center justify-center gap-1 sm:gap-1.5 sm:w-full min-h-[40px] sm:min-h-[44px] px-3 sm:px-0 py-1 rounded-xl transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111] dark:focus-visible:outline-[#ECEBE4] cursor-pointer ${
                isActive
                  ? 'text-[#111111] dark:text-[#ECEBE4]'
                  : 'text-[#8C8C8C] dark:text-[#96948A] hover:text-[#111111] dark:hover:text-[#ECEBE4]'
              }`}
            >
              <span className="transition-transform duration-150 group-hover:-translate-y-0.5">
                {item.renderIcon(isActive)}
              </span>
              <span
                className={`font-mono text-[11px] leading-none whitespace-nowrap shrink-0 ${
                  isActive
                    ? 'font-semibold text-[#111111] dark:text-[#ECEBE4]'
                    : 'font-normal'
                }`}
              >
                {item.label}
              </span>
            </button>
          );
        })}
        <button type="button" onClick={onToggleHistory} aria-expanded={historyOpen}
          aria-controls={historyOpen ? 'chat-history' : undefined}
          className="flex min-h-10 flex-col items-center justify-center gap-1 rounded-xl px-3 py-1 font-mono text-[11px] text-neutral-700 hover:bg-neutral-100 dark:text-[#ECEBE4] dark:hover:bg-neutral-800 sm:w-full sm:px-0"
        >
          <History className="size-5" aria-hidden="true" /> History
        </button>
      </nav>

      <button
        type="button"
        onClick={() => onSelectSection('Logout')}
        className="group flex flex-col items-center justify-center gap-1 sm:gap-1.5 sm:w-full min-h-[40px] sm:min-h-[44px] px-3 sm:px-0 py-1 rounded-xl text-[#8C8C8C] dark:text-[#96948A] hover:text-[#111111] dark:hover:text-[#ECEBE4] transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111] dark:focus-visible:outline-[#ECEBE4] cursor-pointer shrink-0"
      >
        <LogOut
          className="w-5 h-5 transition-transform duration-150 group-hover:-translate-y-0.5"
          strokeWidth={1.6}
        />
        <span className="font-mono text-[11px] leading-none whitespace-nowrap shrink-0">
          Logout
        </span>
      </button>
    </aside>
  );
}
