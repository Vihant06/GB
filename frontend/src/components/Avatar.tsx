import {useState} from 'react';

interface AvatarProps {
  src: string;
  name: string;
  bgColor?: string;
  online?: boolean;
  showPresenceDot?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

export function Avatar({
  src,
  name,
  bgColor = '#E5E7EB',
  online = false,
  showPresenceDot = true,
  size = 'md',
}: AvatarProps) {
  const [imgError, setImgError] = useState(false);

  const sizeClasses = {
    sm: 'w-9 h-9 text-xs',
    md: 'w-11 h-11 text-sm',
    lg: 'w-12 h-12 text-sm',
  }[size];

  const dotClasses = {
    sm: 'w-2.5 h-2.5 -bottom-0.5 -right-0.5',
    md: 'w-3 h-3 bottom-0 right-0',
    lg: 'w-3 h-3 bottom-0 right-0',
  }[size];

  const initials = name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className={`relative inline-flex shrink-0 ${sizeClasses}`}>
      <div
        className="w-full h-full rounded-full border-[1.5px] border-[#111111] dark:border-[#42413A] overflow-hidden flex items-center justify-center font-semibold text-[#111111]"
        style={{backgroundColor: bgColor}}
      >
        {!imgError ? (
          <img
            src={src}
            alt={name}
            referrerPolicy="no-referrer"
            onError={() => setImgError(true)}
            className="w-full h-full object-cover"
          />
        ) : (
          <span>{initials}</span>
        )}
      </div>
      {showPresenceDot && online && (
        <span
          aria-label="Online"
          className={`absolute rounded-full bg-[#32B749] border-[1.5px] border-white dark:border-[#181815] ${dotClasses}`}
        />
      )}
    </div>
  );
}
