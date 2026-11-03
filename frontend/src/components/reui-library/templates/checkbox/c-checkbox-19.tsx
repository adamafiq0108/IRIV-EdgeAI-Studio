import { Badge } from "@/components/reui-library/reui/badge"

import { Checkbox } from "@/components/reui-library/ui/checkbox"
import { Field, FieldLabel } from "@/components/reui-library/ui/field"

export default function Pattern() {
  return (
    <Field className="w-auto">
      <Field orientation="horizontal">
        <Checkbox id="checkbox-badge-1" />
        <div className="flex items-center gap-3">
          <FieldLabel htmlFor="checkbox-badge-1">
            AI-powered suggestions
          </FieldLabel>
          <Badge className="rounded-full uppercase" size="sm">
            New
          </Badge>
        </div>
      </Field>
      <Field orientation="horizontal" defaultChecked>
        <Checkbox id="checkbox-badge-2" />
        <div className="flex items-center gap-3">
          <FieldLabel htmlFor="checkbox-badge-2">
            Beta feature access
          </FieldLabel>
          <Badge
            variant="secondary"
            className="rounded-full uppercase"
            size="sm"
          >
            Beta
          </Badge>
        </div>
      </Field>
    </Field>
  )
}
