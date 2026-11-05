import { Info } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** A small "i" that explains a number in plain words. */
export function MetricHelp({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger type="button" aria-label={text} className="inline-flex cursor-help text-muted-foreground/70 hover:text-foreground">
        <Info className="size-3" />
      </TooltipTrigger>
      <TooltipContent className="max-w-64">{text}</TooltipContent>
    </Tooltip>
  );
}
