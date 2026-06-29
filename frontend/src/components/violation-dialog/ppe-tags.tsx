"use client";

import { Badge } from "@/components/ui/badge";
import { formatSafetyLabel } from "@/lib/formatters";
import { AlertTriangle, ShieldOff } from "lucide-react";

interface PPETagsProps {
  missingPPE: string[];
  maxDisplay?: number;
}

export function PPETags({ missingPPE, maxDisplay = 5 }: PPETagsProps) {
  if (!missingPPE || missingPPE.length === 0) {
    return null;
  }

  const displayTags = missingPPE.slice(0, maxDisplay);
  const remainingCount = missingPPE.length - maxDisplay;

  return (
    <div className="flex flex-wrap gap-2">
      {displayTags.map((ppe) => {
        const isActionViolation = ppe.startsWith("action:");
        const displayText = isActionViolation
          ? ppe.replace("action:", "").replace(/_/g, " ")
          : formatSafetyLabel(ppe);

        return (
          <Badge
            key={ppe}
            variant="destructive"
            className="flex items-center gap-1.5 px-3 py-1.5"
          >
            {isActionViolation ? (
              <AlertTriangle className="w-3 h-3" />
            ) : (
              <ShieldOff className="w-3 h-3" />
            )}
            {displayText}
          </Badge>
        );
      })}
      {remainingCount > 0 && (
        <Badge variant="outline" className="px-3 py-1.5">
          +{remainingCount} 更多
        </Badge>
      )}
    </div>
  );
}
