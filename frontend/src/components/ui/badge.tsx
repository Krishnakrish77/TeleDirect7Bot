import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex w-fit shrink-0 items-center rounded-full border px-2 py-0.5 text-[0.7rem] font-bold leading-none',
  {
    variants: {
      variant: {
        default: 'border-[color:color-mix(in_srgb,var(--brand)_36%,transparent)] bg-[color:color-mix(in_srgb,var(--brand)_12%,transparent)] text-[#fdba74]',
        success: 'border-[rgba(20,184,166,0.32)] bg-[rgba(20,184,166,0.1)] text-[#99f6e4]',
        muted: 'border-[var(--line)] bg-white/4 text-[var(--muted)]',
        destructive: 'border-[rgba(251,113,133,0.34)] bg-[rgba(251,113,133,0.1)] text-[#fda4af]'
      }
    },
    defaultVariants: { variant: 'default' }
  }
);

function Badge({ className, variant, ...props }: React.ComponentProps<'span'> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
