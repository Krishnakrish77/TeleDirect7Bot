import * as React from 'react';
import * as SliderPrimitive from '@radix-ui/react-slider';

import { cn } from '@/lib/utils';

// Reusable shadcn-style slider (Radix) — used for the now-playing scrubber and
// the volume control. Accessible + touch-friendly, unlike a raw range input.
function Slider({ className, ...props }: React.ComponentProps<typeof SliderPrimitive.Root>) {
  return (
    <SliderPrimitive.Root
      data-slot="slider"
      className={cn(
        'relative flex w-full touch-none select-none items-center data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-[var(--line-strong)]">
        <SliderPrimitive.Range className="absolute h-full rounded-full bg-[var(--brand)]" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="block size-4 shrink-0 rounded-full bg-white shadow-[0_2px_6px_rgba(0,0,0,0.45)] outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)]" />
    </SliderPrimitive.Root>
  );
}

export { Slider };
