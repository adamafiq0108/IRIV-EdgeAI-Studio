"use client"

import { useState } from "react"

import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldTitle,
} from "@/components/reui-library/ui/field"
import { Slider } from "@/components/reui-library/ui/slider"
import { Switch } from "@/components/reui-library/ui/switch"

export default function Pattern() {
  const [cpuLimit, setCpuLimit] = useState([75])

  return (
    <div className="mx-auto w-full max-w-xs">
      <FieldGroup>
        <Field orientation="horizontal">
          <FieldContent>
            <FieldTitle>High Performance Mode</FieldTitle>
            <FieldDescription>
              Prioritize speed over battery life for intensive tasks.
            </FieldDescription>
          </FieldContent>
          <Switch id="high-performance" defaultChecked />
        </Field>
        <Field>
          <div className="flex items-center justify-between">
            <FieldLabel htmlFor="cpu-allocation">CPU Allocation</FieldLabel>
            <span className="text-muted-foreground text-xs font-medium">
              {cpuLimit[0]}%
            </span>
          </div>
          <Slider
            id="cpu-allocation"
            value={cpuLimit}
            onValueChange={(value) => setCpuLimit(value as number[])}
            max={100}
            step={5}
          />
          <FieldDescription>
            Limit the maximum CPU resources used by the application.
          </FieldDescription>
        </Field>
      </FieldGroup>
    </div>
  )
}
