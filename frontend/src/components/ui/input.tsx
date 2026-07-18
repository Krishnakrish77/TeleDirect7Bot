import * as React from 'react';

import { cn } from '@/lib/utils';

function Input({ className, type, ...props }: React.ComponentProps<'input'>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn('flex h-10 w-full min-w-0 rounded-lg border border-[var(--line)] bg-[var(--bg-soft)] px-3 py-1 text-sm text-[var(--text)] shadow-xs outline-none transition-[border-color,box-shadow] placeholder:text-[var(--dim)] focus-visible:border-[var(--brand)] focus-visible:ring-2 focus-visible:ring-[color:color-mix(in_srgb,var(--brand)_30%,transparent)] disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50', className)}
      {...props}
    />
  );
}

export { Input };
