import * as React from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';

import { CheckIcon, ChevronDownIcon, ChevronUpIcon } from '@/icons';
import { cn } from '@/lib/utils';

const Select = SelectPrimitive.Root;
const SelectGroup = SelectPrimitive.Group;
const SelectValue = SelectPrimitive.Value;

function SelectTrigger({ className, children, ...props }: React.ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      className={cn('flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-[var(--line)] bg-[var(--bg-soft)] px-3 py-2 text-left text-sm text-[var(--text)] outline-none transition-[border-color,box-shadow] data-[placeholder]:text-[var(--dim)] focus:border-[var(--brand)] focus-visible:ring-2 focus-visible:ring-[color:color-mix(in_srgb,var(--brand)_30%,transparent)] disabled:cursor-not-allowed disabled:opacity-50 [&>span]:line-clamp-1', className)}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild><ChevronDownIcon className="size-4 shrink-0 text-[var(--muted)]" /></SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

function SelectContent({ className, children, position = 'popper', ...props }: React.ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        data-slot="select-content"
        position={position}
        className={cn('z-50 max-h-72 min-w-[8rem] overflow-hidden rounded-lg border border-[var(--line-strong)] bg-[var(--panel-strong)] p-1 text-[var(--text)] shadow-xl data-[state=open]:animate-in data-[state=closed]:animate-out', position === 'popper' && 'translate-y-1', className)}
        {...props}
      >
        <SelectPrimitive.ScrollUpButton className="flex h-7 cursor-default items-center justify-center text-[var(--muted)]"><ChevronUpIcon className="size-4" /></SelectPrimitive.ScrollUpButton>
        <SelectPrimitive.Viewport className={cn('p-1', position === 'popper' && 'min-w-[var(--radix-select-trigger-width)]')}>
          {children}
        </SelectPrimitive.Viewport>
        <SelectPrimitive.ScrollDownButton className="flex h-7 cursor-default items-center justify-center text-[var(--muted)]"><ChevronDownIcon className="size-4" /></SelectPrimitive.ScrollDownButton>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

function SelectItem({ className, children, ...props }: React.ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn('relative flex w-full cursor-default select-none items-center rounded-md py-2 pl-8 pr-3 text-sm outline-none data-[highlighted]:bg-[color:color-mix(in_srgb,var(--brand)_18%,transparent)] data-[highlighted]:text-white data-[state=checked]:text-[#fdba74] data-[disabled]:pointer-events-none data-[disabled]:opacity-50', className)}
      {...props}
    >
      <span className="absolute left-2 grid size-4 place-items-center"><SelectPrimitive.ItemIndicator><CheckIcon className="size-3.5" /></SelectPrimitive.ItemIndicator></span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}

function SelectLabel({ className, ...props }: React.ComponentProps<typeof SelectPrimitive.Label>) {
  return <SelectPrimitive.Label className={cn('px-2 py-1.5 text-xs font-semibold text-[var(--muted)]', className)} {...props} />;
}

function SelectSeparator({ className, ...props }: React.ComponentProps<typeof SelectPrimitive.Separator>) {
  return <SelectPrimitive.Separator className={cn('-mx-1 my-1 h-px bg-[var(--line)]', className)} {...props} />;
}

export { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue };
