"use client"

import { addDays } from "date-fns"

import { Calendar } from "@/components/reui-library/ui/calendar"
import { Card, CardContent } from "@/components/reui-library/ui/card"

export default function Pattern() {
  const today = new Date()

  return (
    <Card className="p-0">
      <CardContent className="p-0">
        <Calendar
          disabled={[
            { before: new Date() },
            new Date(),
            { dayOfWeek: [0, 6] },
            {
              from: addDays(today, 14),
              to: addDays(today, 16),
            },
            {
              from: addDays(today, 23),
              to: addDays(today, 24),
            },
          ]}
          excludeDisabled
          mode="range"
        />
      </CardContent>
    </Card>
  )
}
