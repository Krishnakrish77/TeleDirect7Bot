import * as React from 'react';

import { cn } from '@/lib/utils';

function Progress({ value = 0, className, indicatorClassName, ...props }: React.ComponentProps<'div'> & { value?: number; indicatorClassName?: string }) {
  const normalized = Math.max(0, Math.min(100, value));
  return (
    <div
      data-slot="progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={normalized}
      className={cn('h-1.5 w-full overflow-hidden rounded-full bg-white/10', className)}
      {...props}
    >
      <div
        data-slot="progress-indicator"
        className={cn('h-full rounded-full bg-[var(--teal)] transition-[width] duration-300', indicatorClassName)}
        style={{ width: `${normalized}%` }}
      />
    </div>
  );
}

export { Progress };
