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

export function ChevronRightIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="m9 18 6-6-6-6" />
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

export function XIcon(props: Props) {
  return (
    <IconBase {...props}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </IconBase>
  );
}
