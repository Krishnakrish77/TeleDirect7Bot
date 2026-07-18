import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold transition-[color,background-color,border-color,box-shadow,transform] duration-150 outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)] disabled:pointer-events-none disabled:opacity-50 active:scale-[0.98]',
  {
    variants: {
      variant: {
        default: 'bg-[var(--brand)] text-white shadow-[0_8px_20px_rgba(249,115,22,0.24)] hover:bg-[#ea580c]',
        secondary: 'border border-[var(--line-strong)] bg-[var(--panel)] text-[var(--text)] hover:bg-[var(--panel-strong)]',
        ghost: 'text-[var(--muted)] hover:bg-white/8 hover:text-[var(--text)]',
        outline: 'border border-[var(--line)] bg-transparent text-[var(--text)] hover:border-[var(--line-strong)] hover:bg-white/5',
        destructive: 'bg-[#e5484d] text-white hover:bg-[#d93d43]'
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-8 rounded-md px-3 text-xs',
        lg: 'h-12 px-5 text-base',
        icon: 'size-10 p-0',
        'icon-sm': 'size-8 rounded-md p-0'
      }
    },
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
);

type ButtonProps = React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  };

function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button';

  return <Comp data-slot="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}

export { Button, buttonVariants };
