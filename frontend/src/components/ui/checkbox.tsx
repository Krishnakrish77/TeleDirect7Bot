import * as React from 'react';
import * as CheckboxPrimitive from '@radix-ui/react-checkbox';

import { CheckIcon } from '@/icons';
import { cn } from '@/lib/utils';

function Checkbox({ className, ...props }: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn('peer inline-flex size-5 shrink-0 items-center justify-center appearance-none rounded-md border border-[var(--line-strong)] bg-[var(--bg-soft)] text-[var(--bg)] outline-none transition-[background-color,border-color,box-shadow] hover:border-[var(--brand)] focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)] data-[state=checked]:border-[var(--teal)] data-[state=checked]:bg-[var(--teal)] disabled:cursor-not-allowed disabled:opacity-50', className)}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center text-[var(--bg)]">
        <CheckIcon className="size-3.5" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
