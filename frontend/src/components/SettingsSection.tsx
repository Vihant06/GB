import {Moon, Sun, Trash2} from 'lucide-react';
import {ResponseStyle, ThemeMode} from '../types/chat';

interface SettingsSectionProps {
  theme: ThemeMode;
  onChangeTheme: (theme: ThemeMode) => void;
  responseStyle: ResponseStyle;
  onChangeResponseStyle: (style: ResponseStyle) => void;
  onClearChat: () => void;
}

export function SettingsSection({
  theme,
  onChangeTheme,
  responseStyle,
  onChangeResponseStyle,
  onClearChat,
}: SettingsSectionProps) {
  const styles: Array<{id: ResponseStyle; label: string; desc: string}> = [
    {
      id: 'concise',
      label: 'Concise',
      desc: 'Short, direct answers in 2-4 sentences.',
    },
    {
      id: 'balanced',
      label: 'Balanced',
      desc: 'Conversational and well-structured replies.',
    },
    {
      id: 'detailed',
      label: 'Detailed',
      desc: 'In-depth explanations with step-by-step detail.',
    },
  ];

  const themeOptions: Array<{
    id: ThemeMode;
    label: string;
    desc: string;
    swatchBg: string;
    swatchBorder: string;
    icon: React.ReactNode;
  }> = [
    {
      id: 'light',
      label: 'Light Paper',
      desc: 'Warm off-white canvas with crisp ink strokes.',
      swatchBg: '#FAFAF5',
      swatchBorder: '#111111',
      icon: <Sun className="w-4 h-4" strokeWidth={1.9} />,
    },
    {
      id: 'dark',
      label: 'Warm Dark',
      desc: 'Low-glare charcoal paper with muted ink lines.',
      swatchBg: '#20201C',
      swatchBorder: '#42413A',
      icon: <Moon className="w-4 h-4" strokeWidth={1.9} />,
    },
  ];

  return (
    <section
      aria-label="Chat Settings"
      className="flex-1 flex flex-col items-center justify-center px-5 sm:px-10 py-8 overflow-y-auto chat-scroll bg-white dark:bg-[#20201C] transition-colors duration-150"
    >
      <div className="w-full max-w-[560px] space-y-5">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-[#111111] dark:text-[#ECEBE4]">
            Chat Preferences
          </h2>
          <p className="mt-1 font-mono text-xs text-neutral-500 dark:text-[#A3A196]">
            Customize appearance and how Nemotron responds in your 1-to-1 sessions.
          </p>
        </div>

        {/* Appearance / Theme Toggle */}
        <div className="rounded-[20px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] p-5 shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C]">
          <p className="text-sm font-semibold text-[#111111] dark:text-[#ECEBE4] mb-3">
            Appearance
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {themeOptions.map((opt) => {
              const isSelected = theme === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => onChangeTheme(opt.id)}
                  aria-pressed={isSelected}
                  className={`rounded-2xl border-[1.75px] border-[#111111] dark:border-[#42413A] p-3.5 text-left transition-colors duration-150 flex items-start gap-3 cursor-pointer ${
                    isSelected
                      ? 'bg-[#9BD5F7] dark:bg-[#7DB9DE] text-[#111111] dark:text-[#181815]'
                      : 'bg-white dark:bg-[#20201C] text-[#111111] dark:text-[#ECEBE4] hover:bg-[#F7EA78]/40 dark:hover:bg-[#D8CB68]/20'
                  }`}
                >
                  <span
                    className="w-8 h-8 rounded-full border-[1.5px] flex items-center justify-center shrink-0 mt-0.5"
                    style={{
                      backgroundColor: opt.swatchBg,
                      borderColor: opt.swatchBorder,
                      color: opt.id === 'dark' ? '#ECEBE4' : '#111111',
                    }}
                  >
                    {opt.icon}
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold">{opt.label}</p>
                    <p
                      className={`mt-1 font-mono text-[11px] leading-snug ${
                        isSelected
                          ? 'text-[#111111]/80 dark:text-[#181815]/85'
                          : 'text-neutral-600 dark:text-[#A3A196]'
                      }`}
                    >
                      {opt.desc}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Response Style */}
        <div className="rounded-[20px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-[#FAFAF5] dark:bg-[#282823] p-5 shadow-[0_3px_0_0_#111111] dark:shadow-[0_3px_0_0_#0E0E0C]">
          <p className="text-sm font-semibold text-[#111111] dark:text-[#ECEBE4] mb-3">
            Response Style
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
            {styles.map((option) => {
              const isSelected = responseStyle === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => onChangeResponseStyle(option.id)}
                  aria-pressed={isSelected}
                  className={`rounded-2xl border-[1.75px] border-[#111111] dark:border-[#42413A] p-3 text-left transition-colors cursor-pointer ${
                    isSelected
                      ? 'bg-[#9BD5F7] dark:bg-[#7DB9DE] text-[#111111] dark:text-[#181815]'
                      : 'bg-white dark:bg-[#20201C] text-[#111111] dark:text-[#ECEBE4] hover:bg-[#F7EA78]/40 dark:hover:bg-[#D8CB68]/20'
                  }`}
                >
                  <p className="text-xs font-semibold">{option.label}</p>
                  <p
                    className={`mt-1 font-mono text-[11px] leading-snug ${
                      isSelected
                        ? 'text-[#111111]/80 dark:text-[#181815]/85'
                        : 'text-neutral-600 dark:text-[#A3A196]'
                    }`}
                  >
                    {option.desc}
                  </p>
                </button>
              );
            })}
          </div>
        </div>

        {/* Conversation History */}
        <div className="rounded-[20px] border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#282823] p-5 flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-semibold text-[#111111] dark:text-[#ECEBE4]">
              Conversation History
            </p>
            <p className="mt-0.5 font-mono text-xs text-neutral-500 dark:text-[#A3A196]">
              Clear all messages from your current session.
            </p>
          </div>
          <button
            type="button"
            onClick={onClearChat}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-full border-[1.75px] border-[#111111] dark:border-[#42413A] bg-white dark:bg-[#20201C] hover:bg-red-50 dark:hover:bg-red-950/40 text-xs font-medium text-red-700 dark:text-red-400 whitespace-nowrap shrink-0 cursor-pointer"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Clear Chat</span>
          </button>
        </div>
      </div>
    </section>
  );
}
