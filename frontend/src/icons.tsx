import type { SVGProps } from 'react';

type Props = SVGProps<SVGSVGElement>;

function IconBase(props: Props) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    />
  );
}

export function SearchIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m21 21-4.34-4.34" />
      <circle cx="11" cy="11" r="7" />
    </IconBase>
  );
}

export function PlayIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M8 5v14l11-7Z" />
    </IconBase>
  );
}

export function PauseIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M8 5v14" />
      <path d="M16 5v14" />
    </IconBase>
  );
}

export function SkipBackIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m19 20-10-8 10-8v16Z" />
      <path d="M5 19V5" />
    </IconBase>
  );
}

export function SkipForwardIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m5 4 10 8-10 8V4Z" />
      <path d="M19 5v14" />
    </IconBase>
  );
}

export function ShuffleIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M16 3h5v5" />
      <path d="M4 20 21 3" />
      <path d="M21 16v5h-5" />
      <path d="m15 15 6 6" />
      <path d="M4 4h3l4 4" />
    </IconBase>
  );
}

export function RepeatIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m17 2 4 4-4 4" />
      <path d="M3 11V9a4 4 0 0 1 4-4h14" />
      <path d="m7 22-4-4 4-4" />
      <path d="M21 13v2a4 4 0 0 1-4 4H3" />
    </IconBase>
  );
}

export function BookmarkIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M6 4h12a1 1 0 0 1 1 1v16l-7-4-7 4V5a1 1 0 0 1 1-1Z" />
    </IconBase>
  );
}

export function CheckIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M20 6 9 17l-5-5" />
    </IconBase>
  );
}

export function ThumbUpIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M7 10v11" />
      <path d="M15 6.5 14 10h5.2a2 2 0 0 1 1.94 2.5l-1.4 5.5A4 4 0 0 1 15.86 21H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h2.3a2 2 0 0 0 1.7-1l3.2-5.3a2.2 2.2 0 0 1 4.1 1.4Z" />
    </IconBase>
  );
}

export function ThumbDownIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M17 14V3" />
      <path d="M9 17.5 10 14H4.8a2 2 0 0 1-1.94-2.5l1.4-5.5A4 4 0 0 1 8.14 3H20a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-2.3a2 2 0 0 0-1.7 1l-3.2 5.3a2.2 2.2 0 0 1-4.1-1.4Z" />
    </IconBase>
  );
}

export function ChevronRightIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m9 18 6-6-6-6" />
    </IconBase>
  );
}

export function ChevronDownIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m6 9 6 6 6-6" />
    </IconBase>
  );
}

export function ChevronUpIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m6 15 6-6 6 6" />
    </IconBase>
  );
}

export function FilterIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M4 6h16" />
      <path d="M7 12h10" />
      <path d="M10 18h4" />
    </IconBase>
  );
}

export function ChartIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M4 19V5" />
      <path d="M4 19h16" />
      <path d="M8 16v-5" />
      <path d="M13 16V8" />
      <path d="M18 16v-3" />
    </IconBase>
  );
}

export function ShieldIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
      <path d="M9 12l2 2 4-4" />
    </IconBase>
  );
}

export function LogOutIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </IconBase>
  );
}

export function UserIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M19 21a7 7 0 0 0-14 0" />
      <circle cx="12" cy="8" r="4" />
    </IconBase>
  );
}

export function MusicIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </IconBase>
  );
}

export function FilmIcon(props: Props) {
  return (
    <IconBase {...props}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M8 5v14" />
      <path d="M16 5v14" />
      <path d="M3 10h5" />
      <path d="M16 10h5" />
      <path d="M3 14h5" />
      <path d="M16 14h5" />
    </IconBase>
  );
}

export function HomeIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m3 11 9-8 9 8" />
      <path d="M5 10v10h14V10" />
      <path d="M9 20v-6h6v6" />
    </IconBase>
  );
}

export function ListIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M8 6h13" />
      <path d="M8 12h13" />
      <path d="M8 18h13" />
      <path d="M3 6h.01" />
      <path d="M3 12h.01" />
      <path d="M3 18h.01" />
    </IconBase>
  );
}

export function ListPlusIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M11 6h10" />
      <path d="M11 12h10" />
      <path d="M11 18h10" />
      <path d="M3 12h6" />
      <path d="M6 9v6" />
    </IconBase>
  );
}

export function VolumeIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M11 5 6 9H3v6h3l5 4V5Z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.5 5.5a9 9 0 0 1 0 13" />
    </IconBase>
  );
}

export function CaptionsIcon(props: Props) {
  return (
    <IconBase {...props}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M7 12h2" />
      <path d="M11 12h6" />
      <path d="M7 15h5" />
      <path d="M14 15h3" />
    </IconBase>
  );
}

export function MaximizeIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M8 3H5a2 2 0 0 0-2 2v3" />
      <path d="M16 3h3a2 2 0 0 1 2 2v3" />
      <path d="M8 21H5a2 2 0 0 1-2-2v-3" />
      <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
    </IconBase>
  );
}

export function PictureInPictureIcon(props: Props) {
  return (
    <IconBase {...props}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <rect x="12" y="12" width="6" height="4" rx="1" />
    </IconBase>
  );
}

export function DownloadIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M5 21h14" />
    </IconBase>
  );
}

export function ShareIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
      <path d="m16 6-4-4-4 4" />
      <path d="M12 2v13" />
    </IconBase>
  );
}

export function MoreVerticalIcon(props: Props) {
  return (
    <IconBase {...props} fill="currentColor" stroke="none">
      <circle cx="12" cy="5" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="12" cy="19" r="1.6" />
    </IconBase>
  );
}

export function XIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </IconBase>
  );
}
