import * as React from 'react';

import { cn } from '@/lib/utils';

function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) {
  return (
    <textarea
      data-slot="textarea"
      className={cn('flex min-h-24 w-full rounded-lg border border-[var(--line)] bg-[var(--bg-soft)] px-3 py-2 text-sm text-[var(--text)] shadow-xs outline-none transition-[border-color,box-shadow] placeholder:text-[var(--dim)] focus-visible:border-[var(--brand)] focus-visible:ring-2 focus-visible:ring-[color:color-mix(in_srgb,var(--brand)_30%,transparent)] disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50', className)}
      {...props}
    />
  );
}

export { Textarea };
