"use client"

import { useState } from "react"

import { cn } from "@/components/reui-library/lib/utils"
import { Button } from "@/components/reui-library/ui/button"
import { ButtonGroup } from "@/components/reui-library/ui/button-group"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/reui-library/ui/tooltip"
import { IconPlaceholder } from "@/app/(create)/components/icon-placeholder"

const orderTypes = [
  {
    id: "market",
    label: "Market",
    tooltip: "Execute immediately at best available price",
    icon: (
      <IconPlaceholder
        lucide="TrendingUpIcon"
        tabler="IconTrendingUp"
        hugeicons="TrendUp01Icon"
        phosphor="TrendUpIcon"
        remixicon="RiStockLine"
        aria-hidden="true"
      />
    ),
  },
  {
    id: "limit",
    label: "Limit",
    tooltip: "Execute only at your specified price or better",
    icon: (
      <IconPlaceholder
        lucide="TargetIcon"
        tabler="IconTarget"
        hugeicons="Target01Icon"
        phosphor="TargetIcon"
        remixicon="RiFocus3Line"
        aria-hidden="true"
      />
    ),
  },
  {
    id: "stop",
    label: "Stop",
    tooltip: "Trigger a market order at stop level",
    icon: (
      <IconPlaceholder
        lucide="ShieldAlertIcon"
        tabler="IconShieldExclamation"
        hugeicons="SecurityPasswordIcon"
        phosphor="ShieldWarningIcon"
        remixicon="RiShieldLine"
        aria-hidden="true"
      />
    ),
  },
  {
    id: "stop-limit",
    label: "Stop-Limit",
    tooltip: "Trigger a limit order at stop level",
    icon: (
      <IconPlaceholder
        lucide="ShieldCheckIcon"
        tabler="IconShieldCheck"
        hugeicons="SecurityCheckIcon"
        phosphor="ShieldCheckIcon"
        remixicon="RiShieldCheckLine"
        aria-hidden="true"
      />
    ),
  },
]

export default function Pattern() {
  const [active, setActive] = useState("market")

  return (
    <ButtonGroup>
      {orderTypes.map((type) => (
        <Tooltip key={type.id}>
          <TooltipTrigger
            render={
              <Button
                variant="outline"
                className={cn(active === type.id ? "bg-muted" : "")}
                onClick={() => setActive(type.id)}
              >
                {type.icon}
                {type.label}
              </Button>
            }
          />
          <TooltipContent className="max-w-52 text-center">
            {type.tooltip}
          </TooltipContent>
        </Tooltip>
      ))}
    </ButtonGroup>
  )
}
